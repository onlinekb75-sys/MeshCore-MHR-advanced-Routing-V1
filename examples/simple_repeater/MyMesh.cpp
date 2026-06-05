#include "MyMesh.h"
#include <algorithm>
#include <limits.h>   // MHR Stufe B: INT_MIN

/* ------------------------------ Config -------------------------------- */

#ifndef LORA_FREQ
  #define LORA_FREQ 915.0
#endif
#ifndef LORA_BW
  #define LORA_BW 250
#endif
#ifndef LORA_SF
  #define LORA_SF 10
#endif
#ifndef LORA_CR
  #define LORA_CR 5
#endif
#ifndef LORA_TX_POWER
  #define LORA_TX_POWER 20
#endif

#ifndef ADVERT_NAME
  #define ADVERT_NAME "repeater"
#endif
#ifndef ADVERT_LAT
  #define ADVERT_LAT 0.0
#endif
#ifndef ADVERT_LON
  #define ADVERT_LON 0.0
#endif

#ifndef ADMIN_PASSWORD
  #define ADMIN_PASSWORD "password"
#endif

#ifndef SERVER_RESPONSE_DELAY
  #define SERVER_RESPONSE_DELAY 300
#endif

#ifndef TXT_ACK_DELAY
  #define TXT_ACK_DELAY 200
#endif

#define FIRMWARE_VER_LEVEL       2

#define REQ_TYPE_GET_STATUS         0x01 // same as _GET_STATS
#define REQ_TYPE_KEEP_ALIVE         0x02
#define REQ_TYPE_GET_TELEMETRY_DATA 0x03
#define REQ_TYPE_GET_ACCESS_LIST    0x05
#define REQ_TYPE_GET_NEIGHBOURS     0x06
#define REQ_TYPE_GET_OWNER_INFO     0x07     // FIRMWARE_VER_LEVEL >= 2

#define RESP_SERVER_LOGIN_OK        0 // response to ANON_REQ

#define ANON_REQ_TYPE_REGIONS      0x01
#define ANON_REQ_TYPE_OWNER        0x02
#define ANON_REQ_TYPE_BASIC        0x03   // just remote clock

#define CLI_REPLY_DELAY_MILLIS      600

#define LAZY_CONTACTS_WRITE_DELAY    5000

void MyMesh::putNeighbour(const mesh::Identity &id, uint32_t timestamp, float snr) {
#if MAX_NEIGHBOURS // check if neighbours enabled
  // find existing neighbour, else use least recently updated
  uint32_t oldest_timestamp = 0xFFFFFFFF;
  NeighbourInfo *neighbour = &neighbours[0];
  bool existing = false;
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    // if neighbour already known, we should update it
    if (id.matches(neighbours[i].id)) {
      neighbour = &neighbours[i];
      existing = true;
      break;
    }

    // otherwise we should update the least recently updated neighbour
    if (neighbours[i].heard_timestamp < oldest_timestamp) {
      neighbour = &neighbours[i];
      oldest_timestamp = neighbour->heard_timestamp;
    }
  }

  // update neighbour info
  neighbour->id = id;
  neighbour->advert_timestamp = timestamp;
  neighbour->heard_timestamp = getRTCClock()->getCurrentTime();
  // MHR: EWMA-smooth the link SNR (alpha = 1/4) for a stable link-quality estimate (L0 link sensing),
  //      rather than overwriting with the last raw sample. A fresh/evicted slot is seeded with the sample.
  int8_t sample = (int8_t)(snr * 4);
  neighbour->snr = existing ? (int8_t)(((int)neighbour->snr * 3 + sample) / 4) : sample;
#endif
}

// ====================== MHR Stufe B: redundancy-guarded flood suppression =========================
//  All of the following is dormant unless _prefs.supp_enable == 1 (callers gate on it). Purely local,
//  passive, no packet-format change, never alters hasSeen()/dedup or the forwarding decision itself —
//  the only effect, when enabled and ALL guards prove redundancy, is to drop our OWN already-scheduled
//  rebroadcast at send time (Dispatcher::allowFloodRebroadcast). Default action is always "send".

#if MAX_NEIGHBOURS
// EWMA-SNR (x4) of a known 1-hop neighbour identified by a path-hash prefix; INT_MIN if unknown.
int MyMesh::suppLookupNeighbourSnr(const uint8_t* hash) const {
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    if (neighbours[i].heard_timestamp == 0) continue;
    if (memcmp(neighbours[i].id.pub_key, hash, MHR_SUPP_HASHLEN) == 0) {
      return (int)neighbours[i].snr;  // x4
    }
  }
  return INT_MIN;
}

// G1 degree = number of currently known 1-hop neighbours.
int MyMesh::suppCountKnownNeighbours() const {
  int n = 0;
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    if (neighbours[i].heard_timestamp != 0) n++;
  }
  return n;
}
#else
int MyMesh::suppLookupNeighbourSnr(const uint8_t*) const { return INT_MIN; }
int MyMesh::suppCountKnownNeighbours() const { return 0; }
#endif

// Passive 2-hop table: record that node_hash (a heard sender X) is adjacent to nb_hash, with freshness.
void MyMesh::suppTwoHopAdd(const uint8_t* node_hash, const uint8_t* nb_hash, uint32_t now_secs) {
  if (memcmp(node_hash, nb_hash, MHR_SUPP_HASHLEN) == 0) return;  // don't record self-adjacency

  // find existing row for X, else evict the least-recently-updated (also reuse empty rows)
  SuppTwoHop* row = NULL;
  uint32_t oldest = 0xFFFFFFFF;
  SuppTwoHop* lru = &supp_twohop[0];
  for (int i = 0; i < MHR_SUPP_TWOHOP; i++) {
    SuppTwoHop* r = &supp_twohop[i];
    if (r->updated_secs != 0 && memcmp(r->node_hash, node_hash, MHR_SUPP_HASHLEN) == 0) { row = r; break; }
    if (r->updated_secs < oldest) { oldest = r->updated_secs; lru = r; }
  }
  if (row == NULL) {  // new node X: take LRU slot and reset it
    row = lru;
    memset(row, 0, sizeof(*row));
    memcpy(row->node_hash, node_hash, MHR_SUPP_HASHLEN);
  }
  row->updated_secs = now_secs;

  // upsert nb_hash into X's adjacency list
  for (int j = 0; j < row->num_adj; j++) {
    if (memcmp(row->adj_hash[j], nb_hash, MHR_SUPP_HASHLEN) == 0) return;  // already known
  }
  if (row->num_adj < MHR_SUPP_TWOHOP_ADJ) {
    memcpy(row->adj_hash[row->num_adj], nb_hash, MHR_SUPP_HASHLEN);
    row->num_adj++;
  } else {
    // ring-replace oldest slot (cheap approximation; rows refresh continuously from live traffic)
    memmove(&row->adj_hash[0], &row->adj_hash[1], (MHR_SUPP_TWOHOP_ADJ - 1) * MHR_SUPP_HASHLEN);
    memcpy(row->adj_hash[MHR_SUPP_TWOHOP_ADJ - 1], nb_hash, MHR_SUPP_HASHLEN);
  }
}

// G3 lookup: does heard sender node_hash know neighbour nb_hash, with FRESH 2-hop knowledge?
bool MyMesh::suppNodeKnowsNeighbour(const uint8_t* node_hash, const uint8_t* nb_hash, uint32_t now_secs) const {
  for (int i = 0; i < MHR_SUPP_TWOHOP; i++) {
    const SuppTwoHop* r = &supp_twohop[i];
    if (r->updated_secs == 0) continue;
    if (memcmp(r->node_hash, node_hash, MHR_SUPP_HASHLEN) != 0) continue;
    // freshness gate (Design §3): stale knowledge must NOT make G3 sharp -> treat as "doesn't know"
    if (now_secs - r->updated_secs > MHR_SUPP_FRESH_SECS) return false;
    for (int j = 0; j < r->num_adj; j++) {
      if (memcmp(r->adj_hash[j], nb_hash, MHR_SUPP_HASHLEN) == 0) return true;
    }
    return false;  // X is known, but not adjacent to nb -> not covered by X
  }
  return false;     // X unknown -> unknown coverage -> not covered (conservative)
}

// Free a pending slot once its rebroadcast decision is made (sent or suppressed).
void MyMesh::suppClearPending(SuppPending* p) {
  memset(p, 0, sizeof(*p));
}

// Mark a flood we just scheduled to rebroadcast, so cover senders heard during its backoff get counted.
void MyMesh::suppRecordPending(const mesh::Packet* pkt) {
  uint8_t h[MAX_HASH_SIZE];
  ((mesh::Packet*)pkt)->calculatePacketHash(h);
  uint32_t now = _ms->getMillis();   // ms clock (LRU eviction only; freshness uses RTC secs)

  // already tracked? refresh; else take an empty/LRU slot
  SuppPending* slot = NULL;
  uint32_t oldest = 0xFFFFFFFF;
  SuppPending* lru = &supp_pending[0];
  for (int i = 0; i < MHR_SUPP_PENDING; i++) {
    SuppPending* p = &supp_pending[i];
    if (p->active && memcmp(p->pkt_hash, h, MAX_HASH_SIZE) == 0) { slot = p; break; }
    if (!p->active) { slot = p; break; }
    if (p->heard_at < oldest) { oldest = p->heard_at; lru = p; }
  }
  if (slot == NULL) slot = lru;
  if (!(slot->active && memcmp(slot->pkt_hash, h, MAX_HASH_SIZE) == 0)) {
    memset(slot, 0, sizeof(*slot));
    memcpy(slot->pkt_hash, h, MAX_HASH_SIZE);
    slot->active = true;
  }
  slot->heard_at = now;
}

// Extract a fixed-length MHR_SUPP_HASHLEN key for the path entry at index `idx` (entries are `sz` bytes
// on air; we copy min(sz,HASHLEN) bytes and zero-pad so a mode-mismatch can never read across entries).
static inline void suppPathKey(uint8_t* dst, const mesh::Packet* pkt, uint8_t idx, uint8_t sz) {
  memset(dst, 0, MHR_SUPP_HASHLEN);
  uint8_t n = (sz < MHR_SUPP_HASHLEN) ? sz : MHR_SUPP_HASHLEN;
  memcpy(dst, &pkt->path[idx * sz], n);
}

// Per received flood: passive 2-hop learning + cover-sender counting against any pending rebroadcast.
void MyMesh::suppLearnFromFlood(const mesh::Packet* pkt) {
  uint8_t count = pkt->getPathHashCount();
  uint8_t sz = pkt->getPathHashSize();
  if (count == 0 || sz == 0) return;  // zero-hop (e.g. our direct neighbour's advert) — nothing to chain
  uint32_t now_secs = getRTCClock()->getCurrentTime();

  // -- passive 2-hop learning: consecutive path hops are mutually adjacent (fixed-len keyed, local only) --
  //    path layout: [h0 .. h_{count-1}], h_{count-1} = most recent hop = the node that just sent to us.
  uint8_t a[MHR_SUPP_HASHLEN], b[MHR_SUPP_HASHLEN];
  for (int i = 0; i + 1 < count; i++) {
    suppPathKey(a, pkt, i, sz);
    suppPathKey(b, pkt, i + 1, sz);
    suppTwoHopAdd(a, b, now_secs);
    suppTwoHopAdd(b, a, now_secs);
  }

  // -- cover-sender counting: if this is a DUPLICATE of a flood we have pending, the last hop is a
  //    distinct node that already rebroadcast the same content (a cover sender). Record it (with its
  //    neighbour EWMA-SNR for G4). We never change dedup here — we only observe. --
  uint8_t last[MHR_SUPP_HASHLEN];
  suppPathKey(last, pkt, count - 1, sz);
  uint8_t self_hash[MHR_SUPP_HASHLEN];
  self_id.copyHashTo(self_hash, MHR_SUPP_HASHLEN);
  if (memcmp(last, self_hash, MHR_SUPP_HASHLEN) == 0) return;  // our own echo — not a cover

  uint8_t h[MAX_HASH_SIZE];
  ((mesh::Packet*)pkt)->calculatePacketHash(h);
  for (int i = 0; i < MHR_SUPP_PENDING; i++) {
    SuppPending* p = &supp_pending[i];
    if (!p->active || memcmp(p->pkt_hash, h, MAX_HASH_SIZE) != 0) continue;
    // distinct?
    for (int c = 0; c < p->num_covers; c++) {
      if (memcmp(p->cover_hash[c], last, MHR_SUPP_HASHLEN) == 0) return;  // already counted this sender
    }
    if (p->num_covers < MHR_SUPP_MAX_COVERS) {
      memcpy(p->cover_hash[p->num_covers], last, MHR_SUPP_HASHLEN);
      int snr = suppLookupNeighbourSnr(last);
      p->cover_snr[p->num_covers] = (snr == INT_MIN) ? -128 : (int8_t)snr;  // -128 = unknown SNR
      p->num_covers++;
    }
    return;
  }
}

// Send-time decision: return false ONLY when supp_enable AND all active guards prove redundancy.
bool MyMesh::allowFloodRebroadcast(mesh::Packet* packet) {
  if (!_prefs.supp_enable) return true;          // DEFAULT path: exactly Stufe A — always rebroadcast

  uint8_t h[MAX_HASH_SIZE];
  packet->calculatePacketHash(h);
  SuppPending* p = NULL;
  for (int i = 0; i < MHR_SUPP_PENDING; i++) {
    if (supp_pending[i].active && memcmp(supp_pending[i].pkt_hash, h, MAX_HASH_SIZE) == 0) { p = &supp_pending[i]; break; }
  }
  if (p == NULL) return true;                    // not tracked (e.g. our own reply) -> send

  uint32_t now_secs = getRTCClock()->getCurrentTime();

  // ---- G4 first: count only cover senders with reliable EWMA-SNR >= snr_floor (x4 in storage) ----
  int8_t floor_x4 = (int8_t)(_prefs.supp_snr_floor * 4);
  uint8_t qualified[MHR_SUPP_MAX_COVERS];
  int nq = 0;
  for (int c = 0; c < p->num_covers; c++) {
    if (p->cover_snr[c] == -128) continue;       // unknown SNR cover -> not reliable -> skip (conservative)
    if (p->cover_snr[c] >= floor_x4) { qualified[nq++] = (uint8_t)c; }
  }

  // ---- G2: need >= k_cover distinct qualified cover senders ----
  if (nq < _prefs.supp_k_cover) { suppClearPending(p); return true; }

  // ---- G1: low-degree / leaf protection — never silence below min_degree known neighbours ----
  int degree = suppCountKnownNeighbours();
  if (degree < _prefs.supp_min_degree) { suppClearPending(p); return true; }

#if MAX_NEIGHBOURS
  // ---- G3 (load-bearing): every KNOWN neighbour of ours must be a neighbour of >=1 qualified cover
  //      sender (per the FRESH passive 2-hop table). Unknown coverage = not covered = send. ----
  for (int n = 0; n < MAX_NEIGHBOURS; n++) {
    if (neighbours[n].heard_timestamp == 0) continue;
    const uint8_t* nb = neighbours[n].id.pub_key;   // compare first MHR_SUPP_HASHLEN bytes
    // a cover sender that IS this neighbour trivially covers it (the cover reached it / is it)
    bool covered = false;
    for (int qi = 0; qi < nq && !covered; qi++) {
      const uint8_t* cov = p->cover_hash[qualified[qi]];
      if (memcmp(cov, nb, MHR_SUPP_HASHLEN) == 0) { covered = true; break; }
      if (suppNodeKnowsNeighbour(cov, nb, now_secs)) covered = true;
    }
    if (!covered) { suppClearPending(p); return true; }   // a neighbour might rely on us -> SEND
  }
#else
  // No neighbour table on this build -> G3 cannot be evaluated -> never suppress (safe).
  suppClearPending(p); return true;
#endif

  // ---- G5: probabilistic margin — keep a hard fraction of senders always transmitting ----
  if ((uint32_t)getRNG()->nextInt(0, 100) >= _prefs.supp_prob) { suppClearPending(p); return true; }

  // All active guards proved redundancy -> stay silent.
  suppClearPending(p);
  return false;
}

uint8_t MyMesh::handleLoginReq(const mesh::Identity& sender, const uint8_t* secret, uint32_t sender_timestamp, const uint8_t* data, bool is_flood) {
  ClientInfo* client = NULL;
  if (data[0] == 0) {   // blank password, just check if sender is in ACL
    client = acl.getClient(sender.pub_key, PUB_KEY_SIZE);
    if (client == NULL) {
    #if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Login, sender not in ACL");
    #endif
    }
  }
  if (client == NULL) {
    uint8_t perms;
    if (strcmp((char *)data, _prefs.password) == 0) { // check for valid admin password
      perms = PERM_ACL_ADMIN;
    } else if (strcmp((char *)data, _prefs.guest_password) == 0) { // check guest password
      perms = PERM_ACL_GUEST;
    } else {
#if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Invalid password: %s", data);
#endif
      return 0;
    }

    client = acl.putClient(sender, 0);  // add to contacts (if not already known)
    if (sender_timestamp <= client->last_timestamp) {
      MESH_DEBUG_PRINTLN("Possible login replay attack!");
      return 0;  // FATAL: client table is full -OR- replay attack
    }

    MESH_DEBUG_PRINTLN("Login success!");
    client->last_timestamp = sender_timestamp;
    client->last_activity = getRTCClock()->getCurrentTime();
    client->permissions &= ~0x03;
    client->permissions |= perms;
    memcpy(client->shared_secret, secret, PUB_KEY_SIZE);

    if (perms != PERM_ACL_GUEST) {   // keep number of FS writes to a minimum
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
    }
  }

  if (is_flood) {
    client->out_path_len = OUT_PATH_UNKNOWN;  // need to rediscover out_path
  }

  uint32_t now = getRTCClock()->getCurrentTimeUnique();
  memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  reply_data[4] = RESP_SERVER_LOGIN_OK;
  reply_data[5] = 0;  // Legacy: was recommended keep-alive interval (secs / 16)
  reply_data[6] = client->isAdmin() ? 1 : 0;
  reply_data[7] = client->permissions;
  getRNG()->random(&reply_data[8], 4);   // random blob to help packet-hash uniqueness
  reply_data[12] = FIRMWARE_VER_LEVEL;  // New field

  return 13;  // reply length
}

uint8_t MyMesh::handleAnonRegionsReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)

    return 8 + region_map.exportNamesTo((char *) &reply_data[8], sizeof(reply_data) - 12, REGION_DENY_FLOOD);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonOwnerReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    sprintf((char *) &reply_data[8], "%s\n%s", _prefs.node_name, _prefs.owner_info);

    return 8 + strlen((char *) &reply_data[8]);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonClockReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    reply_data[8] = 0;  // features
#ifdef WITH_RS232_BRIDGE
    reply_data[8] |= 0x01;  // is bridge, type UART
#elif WITH_ESPNOW_BRIDGE
    reply_data[8] |= 0x03;  // is bridge, type ESP-NOW
#endif
    if (_prefs.disable_fwd) {   // is this repeater currently disabled
      reply_data[8] |= 0x80;  // is disabled
    }
    // TODO:  add some kind of moving-window utilisation metric, so can query 'how busy' is this repeater
    return 9;   // reply length
  }
  return 0;
}

int MyMesh::handleRequest(ClientInfo *sender, uint32_t sender_timestamp, uint8_t *payload, size_t payload_len) {
  // uint32_t now = getRTCClock()->getCurrentTimeUnique();
  // memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  memcpy(reply_data, &sender_timestamp, 4); // reflect sender_timestamp back in response packet (kind of like a 'tag')

  if (payload[0] == REQ_TYPE_GET_STATUS) {  // guests can also access this now
    RepeaterStats stats;
    stats.batt_milli_volts = board.getBattMilliVolts();
    stats.curr_tx_queue_len = _mgr->getOutboundTotal();
    stats.noise_floor = (int16_t)_radio->getNoiseFloor();
    stats.last_rssi = (int16_t)radio_driver.getLastRSSI();
    stats.n_packets_recv = radio_driver.getPacketsRecv();
    stats.n_packets_sent = radio_driver.getPacketsSent();
    stats.total_air_time_secs = getTotalAirTime() / 1000;
    stats.total_up_time_secs = uptime_millis / 1000;
    stats.n_sent_flood = getNumSentFlood();
    stats.n_sent_direct = getNumSentDirect();
    stats.n_recv_flood = getNumRecvFlood();
    stats.n_recv_direct = getNumRecvDirect();
    stats.err_events = _err_flags;
    stats.last_snr = (int16_t)(radio_driver.getLastSNR() * 4);
    stats.n_direct_dups = ((SimpleMeshTables *)getTables())->getNumDirectDups();
    stats.n_flood_dups = ((SimpleMeshTables *)getTables())->getNumFloodDups();
    stats.total_rx_air_time_secs = getReceiveAirTime() / 1000;
    stats.n_recv_errors = radio_driver.getPacketsRecvErrors();
    memcpy(&reply_data[4], &stats, sizeof(stats));

    return 4 + sizeof(stats); //  reply_len
  }
  if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA) {
    uint8_t perm_mask = ~(payload[1]); // NEW: first reserved byte (of 4), is now inverse mask to apply to permissions

    telemetry.reset();
    telemetry.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);

    // query other sensors -- target specific
    if ((sender->permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_GUEST) {
      perm_mask = 0x00;  // just base telemetry allowed
    }
    sensors.querySensors(perm_mask, telemetry);

	// This default temperature will be overridden by external sensors (if any)
    float temperature = board.getMCUTemperature();
    if(!isnan(temperature)) { // Supported boards with built-in temperature sensor. ESP32-C3 may return NAN
      telemetry.addTemperature(TELEM_CHANNEL_SELF, temperature); // Built-in MCU Temperature
    }

    uint8_t tlen = telemetry.getSize();
    memcpy(&reply_data[4], telemetry.getBuffer(), tlen);
    return 4 + tlen; // reply_len
  }
  if (payload[0] == REQ_TYPE_GET_ACCESS_LIST && sender->isAdmin()) {
    uint8_t res1 = payload[1];   // reserved for future  (extra query params)
    uint8_t res2 = payload[2];
    if (res1 == 0 && res2 == 0) {
      uint8_t ofs = 4;
      for (int i = 0; i < acl.getNumClients() && ofs + 7 <= sizeof(reply_data) - 4; i++) {
        auto c = acl.getClientByIdx(i);
        if (c->permissions == 0) continue;  // skip deleted entries
        memcpy(&reply_data[ofs], c->id.pub_key, 6); ofs += 6;  // just 6-byte pub_key prefix
        reply_data[ofs++] = c->permissions;
      }
      return ofs;
    }
  }
  if (payload[0] == REQ_TYPE_GET_NEIGHBOURS) {
    uint8_t request_version = payload[1];
    if (request_version == 0) {

      // reply data offset (after response sender_timestamp/tag)
      int reply_offset = 4;

      // get request params
      uint8_t count = payload[2]; // how many neighbours to fetch (0-255)
      uint16_t offset;
      memcpy(&offset, &payload[3], 2); // offset from start of neighbours list (0-65535)
      uint8_t order_by = payload[5]; // how to order neighbours. 0=newest_to_oldest, 1=oldest_to_newest, 2=strongest_to_weakest, 3=weakest_to_strongest
      uint8_t pubkey_prefix_length = payload[6]; // how many bytes of neighbour pub key we want
      // we also send a 4 byte random blob in payload[7...10] to help packet uniqueness

      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS count=%d, offset=%d, order_by=%d, pubkey_prefix_length=%d", count, offset, order_by, pubkey_prefix_length);

      // clamp pub key prefix length to max pub key length
      if(pubkey_prefix_length > PUB_KEY_SIZE){
        pubkey_prefix_length = PUB_KEY_SIZE;
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS invalid pubkey_prefix_length=%d clamping to %d", pubkey_prefix_length, PUB_KEY_SIZE);
      }

      // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
      int16_t neighbours_count = 0;
#if MAX_NEIGHBOURS
      NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
      for (int i = 0; i < MAX_NEIGHBOURS; i++) {
        auto neighbour = &neighbours[i];
        if (neighbour->heard_timestamp > 0) {
          sorted_neighbours[neighbours_count] = neighbour;
          neighbours_count++;
        }
      }

      // sort neighbours based on order
      if (order_by == 0) {
        // sort by newest to oldest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting newest to oldest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->heard_timestamp > b->heard_timestamp; // desc
        });
      } else if (order_by == 1) {
        // sort by oldest to newest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting oldest to newest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->heard_timestamp < b->heard_timestamp; // asc
        });
      } else if (order_by == 2) {
        // sort by strongest to weakest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting strongest to weakest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->snr > b->snr; // desc
        });
      } else if (order_by == 3) {
        // sort by weakest to strongest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting weakest to strongest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->snr < b->snr; // asc
        });
      }
#endif

      // build results buffer
      int results_count = 0;
      int results_offset = 0;
      uint8_t results_buffer[130];
      for(int index = 0; index < count && index + offset < neighbours_count; index++){
        
        // stop if we can't fit another entry in results
        int entry_size = pubkey_prefix_length + 4 + 1;
        if(results_offset + entry_size > sizeof(results_buffer)){
          MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS no more entries can fit in results buffer");
          break;
        }

#if MAX_NEIGHBOURS
        // add next neighbour to results
        auto neighbour = sorted_neighbours[index + offset];
        uint32_t heard_seconds_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
        memcpy(&results_buffer[results_offset], neighbour->id.pub_key, pubkey_prefix_length); results_offset += pubkey_prefix_length;
        memcpy(&results_buffer[results_offset], &heard_seconds_ago, 4); results_offset += 4;
        memcpy(&results_buffer[results_offset], &neighbour->snr, 1); results_offset += 1;
        results_count++;
#endif

      }

      // build reply
      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS neighbours_count=%d results_count=%d", neighbours_count, results_count);
      memcpy(&reply_data[reply_offset], &neighbours_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_buffer, results_offset); reply_offset += results_offset;

      return reply_offset;
    }
  } else if (payload[0] == REQ_TYPE_GET_OWNER_INFO) {
    sprintf((char *) &reply_data[4], "%s\n%s\n%s", FIRMWARE_VERSION, _prefs.node_name, _prefs.owner_info);
    return 4 + strlen((char *) &reply_data[4]);
  }
  return 0; // unknown command
}

mesh::Packet *MyMesh::createSelfAdvert() {
  uint8_t app_data[MAX_ADVERT_DATA_SIZE];
  uint8_t app_data_len = _cli.buildAdvertData(ADV_TYPE_REPEATER, app_data);

  return createAdvert(self_id, app_data, app_data_len);
}

File MyMesh::openAppend(const char *fname) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return _fs->open(fname, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return _fs->open(fname, "a");
#else
  return _fs->open(fname, "a", true);
#endif
}

static uint8_t max_loop_minimal[] =  { 0, /* 1-byte */  4, /* 2-byte */  2, /* 3-byte */  1 };
static uint8_t max_loop_moderate[] = { 0, /* 1-byte */  2, /* 2-byte */  1, /* 3-byte */  1 };
static uint8_t max_loop_strict[] =   { 0, /* 1-byte */  1, /* 2-byte */  1, /* 3-byte */  1 };

bool MyMesh::isLooped(const mesh::Packet* packet, const uint8_t max_counters[]) {
  uint8_t hash_size = packet->getPathHashSize();
  uint8_t hash_count = packet->getPathHashCount();
  uint8_t n = 0;
  const uint8_t* path = packet->path;
  while (hash_count > 0) {      // count how many times this node is already in the path
    if (self_id.isHashMatch(path, hash_size)) n++;
    hash_count--;
    path += hash_size;
  }
  return n >= max_counters[hash_size];
}

void MyMesh::sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size) {
  if (recv_pkt_region && !recv_pkt_region->isWildcard()) {  // if _request_ packet scope is known, send reply with same scope
    TransportKey scope;
    if (region_map.getTransportKeysFor(*recv_pkt_region, &scope, 1) > 0) {
      sendFloodScoped(scope, packet, delay_millis, path_hash_size);
    } else {
      sendFlood(packet, delay_millis, path_hash_size);  // send un-scoped
    }
  } else {
    sendFlood(packet, delay_millis, path_hash_size);  // send un-scoped
  }
}

// MHR: adaptive flood-hop ceiling tunables. Floor must be >= the measured network P90 (=18) so a cold-
//   started node never cuts a legitimate long path before it has observed the diameter.
#ifndef MHR_FLOOD_MAX_FLOOR
  #define MHR_FLOOD_MAX_FLOOR 18
#endif
#ifndef MHR_FLOOD_MAX_MARGIN
  #define MHR_FLOOD_MAX_MARGIN 4
#endif
#ifndef MHR_DIAM_DECAY_MS
  #define MHR_DIAM_DECAY_MS 600000UL   // relax the rolling max by 1 hop every 10 min (follows topology shrink)
#endif

void MyMesh::mhrObserveDiam(const mesh::Packet* packet) {
  if (!packet->isRouteFlood()) return;
  uint8_t h = packet->getPathHashCount();
  if (h > _mhr_obs_diam && h <= 63) _mhr_obs_diam = h;   // track the longest flood path seen through us
}

uint8_t MyMesh::mhrEffectiveFloodMax() {
  // lazy decay so the cap follows the network down after it shrinks (no separate timer needed)
  if (millisHasNowPassed(_mhr_diam_decay_at)) {
    if (_mhr_obs_diam > 0) _mhr_obs_diam--;
    _mhr_diam_decay_at = futureMillis(MHR_DIAM_DECAY_MS);
  }
  uint16_t cap = (uint16_t)_mhr_obs_diam + MHR_FLOOD_MAX_MARGIN;
  if (cap < MHR_FLOOD_MAX_FLOOR) cap = MHR_FLOOD_MAX_FLOOR;
  if (cap > _prefs.flood_max) cap = _prefs.flood_max;   // _prefs.flood_max is now the HARD user ceiling
  return (uint8_t)cap;
}

bool MyMesh::allowPacketForward(const mesh::Packet *packet) {
  if (_prefs.disable_fwd) return false;
  // MHR: adaptive flood-hop limit instead of the fixed _prefs.flood_max (see mhrEffectiveFloodMax()).
  if (packet->isRouteFlood() && packet->getPathHashCount() >= mhrEffectiveFloodMax()) return false;
  if (packet->isRouteFlood() && recv_pkt_region == NULL) {
    MESH_DEBUG_PRINTLN("allowPacketForward: unknown transport code, or wildcard not allowed for FLOOD packet");
    return false;
  }
  if (packet->isRouteFlood() && _prefs.loop_detect != LOOP_DETECT_OFF) {
    const uint8_t* maximums;
    if (_prefs.loop_detect == LOOP_DETECT_MINIMAL) {
      maximums = max_loop_minimal;
    } else if (_prefs.loop_detect == LOOP_DETECT_MODERATE) {
      maximums = max_loop_moderate;
    } else {
      maximums = max_loop_strict;
    }
    if (isLooped(packet, maximums)) {
      MESH_DEBUG_PRINTLN("allowPacketForward: FLOOD packet loop detected!");
      return false;
    }
  }
  return true;
}

const char *MyMesh::getLogDateTime() {
  static char tmp[32];
  uint32_t now = getRTCClock()->getCurrentTime();
  DateTime dt = DateTime(now);
  sprintf(tmp, "%02d:%02d:%02d - %d/%d/%d U", dt.hour(), dt.minute(), dt.second(), dt.day(), dt.month(),
          dt.year());
  return tmp;
}

void MyMesh::logRxRaw(float snr, float rssi, const uint8_t raw[], int len) {
#if MESH_PACKET_LOGGING
  Serial.print(getLogDateTime());
  Serial.print(" RAW: ");
  mesh::Utils::printHex(Serial, raw, len);
  Serial.println();
#endif
}

void MyMesh::logRx(mesh::Packet *pkt, int len, float score) {
  // MHR Stufe B: this hook fires for EVERY received packet (incl. flood duplicates that hasSeen() will
  //   later discard) BEFORE onRecvPacket(). It is the clean, non-invasive observation point for passive
  //   2-hop learning and cover-sender counting. No-op unless supp_enable. Never alters dedup/forwarding.
  if (_prefs.supp_enable && pkt->isRouteFlood()) {
    suppLearnFromFlood(pkt);
  }

#ifdef WITH_BRIDGE
  if (_prefs.bridge_pkt_src == 1) {
    bridge.sendPacket(pkt);
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": RX, len=%d (type=%d, route=%s, payload_len=%d) SNR=%d RSSI=%d score=%d", len,
               pkt->getPayloadType(), pkt->isRouteDirect() ? "D" : "F", pkt->payload_len,
               (int)_radio->getLastSNR(), (int)_radio->getLastRSSI(), (int)(score * 1000));

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTx(mesh::Packet *pkt, int len) {
#ifdef WITH_BRIDGE
  if (_prefs.bridge_pkt_src == 0) {
    bridge.sendPacket(pkt);
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX, len=%d (type=%d, route=%s, payload_len=%d)", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTxFail(mesh::Packet *pkt, int len) {
  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX FAIL!, len=%d (type=%d, route=%s, payload_len=%d)\n", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);
      f.close();
    }
  }
}

int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;
  return (int)((pow(_prefs.rx_delay_base, 0.85f - score) - 1.0) * air_time);
}

// MHR: hop horizon for the rebroadcast-timing lever. Copies that have travelled more than this many hops
//      get no early lead (they are most likely detours). Chosen near the real network diameter (median 10).
#ifndef MHR_HOP_HORIZON
  #define MHR_HOP_HORIZON 12
#endif

uint32_t MyMesh::getRetransmitDelay(const mesh::Packet *packet) {
  // MHR Stufe B: this is exactly the moment routeRecvPacket() has decided to rebroadcast this flood (our
  //   own hash is already appended to the path). Register it as "pending" so cover senders heard during
  //   the backoff window below get counted against it. No-op / zero effect unless supp_enable.
  if (_prefs.supp_enable && packet->isRouteFlood()) {
    suppRecordPending(packet);
  }
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * _prefs.tx_delay_factor);
  uint32_t window = 5*t + 1;
  // MHR: quality-guided flood rebroadcast. A copy that arrived via FEWER accumulated hops (the reliable
  //      signal — the real-data study found SNR correlates only weakly with path length) and/or with
  //      stronger SNR draws its random backoff from a window shrunk toward 0, so it rebroadcasts earlier and
  //      suppresses slower detour copies downstream via hasSeen() dedup. The hop term dominates by default.
  //      Randomness is preserved (the window never shrinks below t+1) to avoid synchronised collisions among
  //      equally-good neighbours. Purely local and mixed-firmware-safe; never worse than upstream — both
  //      weights 0 (or t == 0) reproduce the upstream random backoff exactly. Reversible at runtime:
  //      set txhopweight 0 / set txsnrweight 0.
  if (t > 0 && (_prefs.tx_hop_weight > 0.0f || _prefs.tx_snr_weight > 0.0f)) {
    float q = 0.0f;
    if (_prefs.tx_hop_weight > 0.0f) {
      // fewer hops -> closer to 1 (lead the flood); beyond the hop horizon -> 0 (likely a detour)
      float qhop = 1.0f - (float)packet->getPathHashCount() * (1.0f / (float)MHR_HOP_HORIZON);
      if (qhop < 0.0f) qhop = 0.0f;
      q += _prefs.tx_hop_weight * qhop;
    }
    if (_prefs.tx_snr_weight > 0.0f) {
      float qsnr = (packet->getSNR() + 10.0f) * (1.0f / 20.0f);   // ~0 at -10 dB .. 1 at +10 dB
      if (qsnr < 0.0f) qsnr = 0.0f; else if (qsnr > 1.0f) qsnr = 1.0f;
      q += _prefs.tx_snr_weight * qsnr;
    }
    if (q > 1.0f) q = 1.0f;
    uint32_t hi = window - (uint32_t)(q * (window - t - 1));   // stays >= t+1
    return getRNG()->nextInt(0, hi);
  }
  return getRNG()->nextInt(0, window);
}
uint32_t MyMesh::getDirectRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * _prefs.direct_tx_delay_factor);
  return getRNG()->nextInt(0, 5*t + 1);
}

bool MyMesh::filterRecvFloodPacket(mesh::Packet* pkt) {
  mhrObserveDiam(pkt);   // MHR: feed the adaptive flood-hop ceiling from every flood packet we hear
  // just try to determine region for packet (apply later in allowPacketForward())
  if (pkt->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD) {
    recv_pkt_region = region_map.findMatch(pkt, REGION_DENY_FLOOD);
  } else if (pkt->getRouteType() == ROUTE_TYPE_FLOOD) {
    if (region_map.getWildcard().flags & REGION_DENY_FLOOD) {
      recv_pkt_region = NULL;
    } else {
      recv_pkt_region =  &region_map.getWildcard();
    }
  } else {
    recv_pkt_region = NULL;
  }
  // do normal processing
  return false;
}

void MyMesh::onAnonDataRecv(mesh::Packet *packet, const uint8_t *secret, const mesh::Identity &sender,
                            uint8_t *data, size_t len) {
  if (packet->getPayloadType() == PAYLOAD_TYPE_ANON_REQ) { // received an initial request by a possible admin
                                                           // client (unknown at this stage)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    data[len] = 0;  // ensure null terminator
    uint8_t reply_len;

    reply_path_len = -1;
    if (data[4] == 0 || data[4] >= ' ') {   // is password, ie. a login request
      reply_len = handleLoginReq(sender, secret, timestamp, &data[4], packet->isRouteFlood());
    } else if (data[4] == ANON_REQ_TYPE_REGIONS && packet->isRouteDirect()) {
      reply_len = handleAnonRegionsReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_OWNER && packet->isRouteDirect()) {
      reply_len = handleAnonOwnerReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_BASIC && packet->isRouteDirect()) {
      reply_len = handleAnonClockReq(sender, timestamp, &data[5]);
    } else {
      reply_len = 0;  // unknown/invalid request type
    }

    if (reply_len == 0) return;   // invalid request

    if (packet->isRouteFlood()) {
      // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
      mesh::Packet* path = createPathReturn(sender, secret, packet->path, packet->path_len,
                                            PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
      if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
    } else if (reply_path_len < 0) {
      mesh::Packet* reply = createDatagram(PAYLOAD_TYPE_RESPONSE, sender, secret, reply_data, reply_len);
      if (reply) sendFloodReply(reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
    } else {
      mesh::Packet* reply = createDatagram(PAYLOAD_TYPE_RESPONSE, sender, secret, reply_data, reply_len);
      uint8_t path_len = ((reply_path_hash_size - 1) << 6) | (reply_path_len & 63);
      if (reply) sendDirect(reply, reply_path,  path_len, SERVER_RESPONSE_DELAY);
    }
  }
}

int MyMesh::searchPeersByHash(const uint8_t *hash) {
  int n = 0;
  for (int i = 0; i < acl.getNumClients(); i++) {
    if (acl.getClientByIdx(i)->id.isHashMatch(hash)) {
      matching_peer_indexes[n++] = i; // store the INDEXES of matching contacts (for subsequent 'peer' methods)
    }
  }
  return n;
}

void MyMesh::getPeerSharedSecret(uint8_t *dest_secret, int peer_idx) {
  int i = matching_peer_indexes[peer_idx];
  if (i >= 0 && i < acl.getNumClients()) {
    // lookup pre-calculated shared_secret
    memcpy(dest_secret, acl.getClientByIdx(i)->shared_secret, PUB_KEY_SIZE);
  } else {
    MESH_DEBUG_PRINTLN("getPeerSharedSecret: Invalid peer idx: %d", i);
  }
}

static bool isShare(const mesh::Packet *packet) {
  if (packet->hasTransportCodes()) {
    return packet->transport_codes[0] == 0 && packet->transport_codes[1] == 0;  // codes { 0, 0 } means 'send to nowhere'
  }
  return false;
}

void MyMesh::onAdvertRecv(mesh::Packet *packet, const mesh::Identity &id, uint32_t timestamp,
                          const uint8_t *app_data, size_t app_data_len) {
  mesh::Mesh::onAdvertRecv(packet, id, timestamp, app_data, app_data_len); // chain to super impl

  // if this a zero hop advert (and not via 'Share'), add it to neighbours
  if (packet->path_len == 0 && !isShare(packet)) {
    AdvertDataParser parser(app_data, app_data_len);
    if (parser.isValid() && parser.getType() == ADV_TYPE_REPEATER) { // just keep neigbouring Repeaters
      putNeighbour(id, timestamp, packet->getSNR());
    }
  }
}

void MyMesh::onPeerDataRecv(mesh::Packet *packet, uint8_t type, int sender_idx, const uint8_t *secret,
                            uint8_t *data, size_t len) {
  int i = matching_peer_indexes[sender_idx];
  if (i < 0 || i >= acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("onPeerDataRecv: invalid peer idx: %d", i);
    return;
  }
  ClientInfo* client = acl.getClientByIdx(i);

  if (type == PAYLOAD_TYPE_REQ) { // request (from a Known admin client!)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    if (timestamp > client->last_timestamp) { // prevent replay attacks
      int reply_len = handleRequest(client, timestamp, &data[4], len - 4);
      if (reply_len == 0) return; // invalid command

      client->last_timestamp = timestamp;
      client->last_activity = getRTCClock()->getCurrentTime();

      if (packet->isRouteFlood()) {
        // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
        mesh::Packet *path = createPathReturn(client->id, secret, packet->path, packet->path_len,
                                              PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
        if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
      } else {
        mesh::Packet *reply =
            createDatagram(PAYLOAD_TYPE_RESPONSE, client->id, secret, reply_data, reply_len);
        if (reply) {
          if (client->out_path_len != OUT_PATH_UNKNOWN) { // we have an out_path, so send DIRECT
            sendDirect(reply, client->out_path, client->out_path_len, SERVER_RESPONSE_DELAY);
          } else {
            sendFloodReply(reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
          }
        }
      }
    } else {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
    }
  } else if (type == PAYLOAD_TYPE_TXT_MSG && len > 5 && client->isAdmin()) { // a CLI command
    uint32_t sender_timestamp;
    memcpy(&sender_timestamp, data, 4); // timestamp (by sender's RTC clock - which could be wrong)
    uint8_t flags = (data[4] >> 2);        // message attempt number, and other flags

    if (!(flags == TXT_TYPE_PLAIN || flags == TXT_TYPE_CLI_DATA)) {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: unsupported text type received: flags=%02x", (uint32_t)flags);
    } else if (sender_timestamp >= client->last_timestamp) { // prevent replay attacks
      bool is_retry = (sender_timestamp == client->last_timestamp);
      client->last_timestamp = sender_timestamp;
      client->last_activity = getRTCClock()->getCurrentTime();

      // len can be > original length, but 'text' will be padded with zeroes
      data[len] = 0; // need to make a C string again, with null terminator

      if (flags == TXT_TYPE_PLAIN) { // for legacy CLI, send Acks
        uint32_t ack_hash; // calc truncated hash of the message timestamp + text + sender pub_key, to prove
                           // to sender that we got it
        mesh::Utils::sha256((uint8_t *)&ack_hash, 4, data, 5 + strlen((char *)&data[5]), client->id.pub_key,
                            PUB_KEY_SIZE);

        mesh::Packet *ack = createAck(ack_hash);
        if (ack) {
          if (client->out_path_len == OUT_PATH_UNKNOWN) {
            sendFloodReply(ack, TXT_ACK_DELAY, packet->getPathHashSize());
          } else {
            sendDirect(ack, client->out_path, client->out_path_len, TXT_ACK_DELAY);
          }
        }
      }

      uint8_t temp[166];
      char *command = (char *)&data[5];
      char *reply = (char *)&temp[5];
      if (is_retry) {
        *reply = 0;
      } else {
        handleCommand(sender_timestamp, command, reply);
      }
      int text_len = strlen(reply);
      if (text_len > 0) {
        uint32_t timestamp = getRTCClock()->getCurrentTimeUnique();
        if (timestamp == sender_timestamp) {
          // WORKAROUND: the two timestamps need to be different, in the CLI view
          timestamp++;
        }
        memcpy(temp, &timestamp, 4);        // mostly an extra blob to help make packet_hash unique
        temp[4] = (TXT_TYPE_CLI_DATA << 2); // NOTE: legacy was: TXT_TYPE_PLAIN

        auto reply = createDatagram(PAYLOAD_TYPE_TXT_MSG, client->id, secret, temp, 5 + text_len);
        if (reply) {
          if (client->out_path_len == OUT_PATH_UNKNOWN) {
            sendFloodReply(reply, CLI_REPLY_DELAY_MILLIS, packet->getPathHashSize());
          } else {
            sendDirect(reply, client->out_path, client->out_path_len, CLI_REPLY_DELAY_MILLIS);
          }
        }
      }
    } else {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
    }
  }
}

bool MyMesh::onPeerPathRecv(mesh::Packet *packet, int sender_idx, const uint8_t *secret, uint8_t *path,
                            uint8_t path_len, uint8_t extra_type, uint8_t *extra, uint8_t extra_len) {
  // TODO: prevent replay attacks
  int i = matching_peer_indexes[sender_idx];

  if (i >= 0 && i < acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("PATH to client, path_len=%d", (uint32_t)path_len);
    auto client = acl.getClientByIdx(i);

    // store a copy of path, for sendDirect()
    client->out_path_len = mesh::Packet::copyPath(client->out_path, path, path_len);
    client->last_activity = getRTCClock()->getCurrentTime();
  } else {
    MESH_DEBUG_PRINTLN("onPeerPathRecv: invalid peer idx: %d", i);
  }

  // NOTE: no reciprocal path send!!
  return false;
}

#define CTL_TYPE_NODE_DISCOVER_REQ   0x80
#define CTL_TYPE_NODE_DISCOVER_RESP  0x90

void MyMesh::onControlDataRecv(mesh::Packet* packet) {
  uint8_t type = packet->payload[0] & 0xF0;    // just test upper 4 bits
  if (type == CTL_TYPE_NODE_DISCOVER_REQ && packet->payload_len >= 6
      && !_prefs.disable_fwd && discover_limiter.allow(rtc_clock.getCurrentTime())
  ) {
    int i = 1;
    uint8_t  filter = packet->payload[i++];
    uint32_t tag;
    memcpy(&tag, &packet->payload[i], 4); i += 4;
    uint32_t since;
    if (packet->payload_len >= i+4) {   // optional since field
      memcpy(&since, &packet->payload[i], 4); i += 4;
    } else {
      since = 0;
    }

    if ((filter & (1 << ADV_TYPE_REPEATER)) != 0 && _prefs.discovery_mod_timestamp >= since) {
      bool prefix_only = packet->payload[0] & 1;
      uint8_t data[6 + PUB_KEY_SIZE];
      data[0] = CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER;   // low 4-bits for node type
      data[1] = packet->_snr;   // let sender know the inbound SNR ( x 4)
      memcpy(&data[2], &tag, 4);     // include tag from request, for client to match to
      memcpy(&data[6], self_id.pub_key, PUB_KEY_SIZE);
      auto resp = createControlData(data, prefix_only ? 6 + 8 : 6 + PUB_KEY_SIZE);
      if (resp) {
        sendZeroHop(resp, getRetransmitDelay(resp)*4);  // apply random delay (widened x4), as multiple nodes can respond to this
      }
    }
  } else if (type == CTL_TYPE_NODE_DISCOVER_RESP && packet->payload_len >= 6) {
    uint8_t node_type = packet->payload[0] & 0x0F;
    if (node_type != ADV_TYPE_REPEATER) {
      return;
    }
    if (packet->payload_len < 6 + PUB_KEY_SIZE) {
      MESH_DEBUG_PRINTLN("onControlDataRecv: DISCOVER_RESP pubkey too short: %d", (uint32_t)packet->payload_len);
      return;
    }

    if (pending_discover_tag == 0 || millisHasNowPassed(pending_discover_until)) {
      pending_discover_tag = 0;
      return;
    }
    uint32_t tag;
    memcpy(&tag, &packet->payload[2], 4);
    if (tag != pending_discover_tag) {
      return;
    }

    mesh::Identity id(&packet->payload[6]);
    if (id.matches(self_id)) {
      return;
    }
    putNeighbour(id, rtc_clock.getCurrentTime(), packet->getSNR());
  }
}

void MyMesh::sendNodeDiscoverReq() {
  uint8_t data[10];
  data[0] = CTL_TYPE_NODE_DISCOVER_REQ; // prefix_only=0
  data[1] = (1 << ADV_TYPE_REPEATER);
  getRNG()->random(&data[2], 4); // tag
  memcpy(&pending_discover_tag, &data[2], 4);
  pending_discover_until = futureMillis(60000);
  uint32_t since = 0;
  memcpy(&data[6], &since, 4);

  auto pkt = createControlData(data, sizeof(data));
  if (pkt) {
    sendZeroHop(pkt);
  }
}

// ====================== MHR Phase 2: proactive region backbone (DV control-plane) ==================
//  Every entry point below is gated on _prefs.bb_enable. With bb_enable=0 NONE of these run, so the
//  send/receive path is bit-identical to today. The only on-air effect when enabled is the periodic /
//  trigger-on-change ZERO-HOP PAYLOAD_TYPE_DV packet, which stock nodes ignore (no reflood).
//
//  SCOPE NOTE (deliberately deferred — Design "Realismus" clause):
//   This cut implements the COMPLETE proactive control-plane: DV table, Babel-feasibility + seqno,
//   feasible-successor backup, trigger-on-change (rate-limited), hold-down + route poisoning, and the
//   origin-independent aggregate feasible-distance (Design §3a.1/.2/.3). The route-lookup API
//   (Backbone::lookupRoute) is implemented and gated, ready for the data-path short-circuit.
//   What is NOT yet wired here is the actual DATA-PLANE short-circuit (Backbone-Unicast -> Discovery
//   short-circuit -> Flood). Reason: a repeater forwards OPAQUE encrypted datagrams keyed only by a
//   hash prefix in the path — it has no destination IDENTITY at the forwarding point to look up, and
//   it relays by appending its hash and re-flooding (Mesh::routeRecvPacket, shared by all builds). The
//   "use the backbone if better, else flood" decision belongs at the ORIGINATING endpoint
//   (companion/client build) and would require touching the dedup/path semantics — exactly the
//   high-risk change the project roadmap defers (see CLAUDE.md, Best-of-N dedup caveat). Wiring it
//   speculatively into the shared routeRecvPacket would risk the existing flood-and-cache path, which
//   the hard requirements forbid. Until that endpoint integration lands, lookupRoute() is a pure,
//   side-effect-free query: enabling bb_enable changes ONLY the DV control-plane, never data routing,
//   so "never worse" holds trivially (data always uses today's flood-and-cache).

// Our home region id (the cluster boundary for H1 aggregation). 0 = unknown/none (e.g. no regions set).
uint16_t MyMesh::bbSelfRegionId() const {
  const RegionEntry* r = const_cast<RegionMap&>(region_map).getHomeRegion();
  if (r == NULL) r = const_cast<RegionMap&>(region_map).getDefaultRegion();
  return (r == NULL) ? 0 : r->id;
}

// Build and zero-hop-send one DV update packet (periodic or triggered). No-op unless bb_enable=1.
void MyMesh::bbSendUpdate(bool triggered) {
  if (!_prefs.bb_enable) return;                       // gated: inert when off
  if (_prefs.disable_fwd) return;                      // a non-forwarding node has no backbone role

  uint8_t self_hash[BB_HASH_LEN];
  self_id.copyHashTo(self_hash, BB_HASH_LEN);

  // A repeater that knows more than its single home region acts as an Area Border Router (H1).
  bool is_border = (region_map.getCount() > 1);

  // MHR B1: seqno is now PER-DEST, not per-announce. originateSelf() advances the generation of OUR OWN
  //   (self-owned) dests; learned routes keep their origin's seqno verbatim. There is therefore no longer
  //   a single per-node announce counter here — the old static `self_seqno` was exactly the per-announcer
  //   value that wrongly stamped every relayed entry and defeated the Babel feasibility gate.
  uint32_t now_secs = getRTCClock()->getCurrentTime();
  _backbone.originateSelf(self_hash, bbSelfRegionId(), is_border, now_secs);

  uint8_t payload[MAX_PACKET_PAYLOAD];
  int len = _backbone.buildDVPayload(payload, self_hash, is_border);
  if (len <= 0) return;                                // nothing to advertise yet

  mesh::Packet* pkt = createDVData(payload, len);
  if (pkt) {
    // zero-hop: only direct neighbours process it; never reflooded (mixed-firmware safe). Use a small
    //   independent random delay to de-correlate simultaneous senders; deliberately NOT
    //   getRetransmitDelay() (that path is flood-rebroadcast specific and would tangle with the Stufe-B
    //   suppression bookkeeping for an outbound DV).
    uint32_t delay = getRNG()->nextInt(0, 500);
    sendZeroHop(pkt, delay);
    _backbone.clearDirty();                            // triggered changes have now been propagated
  }
  (void)triggered;
}

void MyMesh::onDVDataRecv(mesh::Packet* packet) {
  if (!_prefs.bb_enable) return;                       // gated: inert when off (default) — discard silently

  uint8_t self_hash[BB_HASH_LEN];
  self_id.copyHashTo(self_hash, BB_HASH_LEN);
  uint32_t now_secs = getRTCClock()->getCurrentTime();
  int rx_snr_x4 = (int)packet->_snr;                   // inbound SNR (x4) as link-quality sample

  bool changed = _backbone.onDVReceived(packet, self_hash, rx_snr_x4, now_secs, _prefs.bb_holddown_s);

  // §3a.1 trigger-on-change (rate-limited): a metric/next-hop change schedules an immediate announce,
  //   but only if the per-node trigger limiter allows; otherwise the change stays "dirty" and rides the
  //   next periodic announce or the next free trigger slot.
  if (changed && bb_trigger_limiter.allow(now_secs)) {
    bbSendUpdate(true);
  }
}

MyMesh::MyMesh(mesh::MainBoard &board, mesh::Radio &radio, mesh::MillisecondClock &ms, mesh::RNG &rng,
               mesh::RTCClock &rtc, mesh::MeshTables &tables)
    : mesh::Mesh(radio, ms, rng, rtc, *new StaticPoolPacketManager(32), tables),
      region_map(key_store), temp_map(key_store),
      _cli(board, rtc, sensors, region_map, acl, &_prefs, this),
      telemetry(MAX_PACKET_PAYLOAD - 4),
      discover_limiter(4, 120),  // max 4 every 2 minutes
      anon_limiter(4, 180),   // max 4 every 3 minutes
      // MHR Phase 2 §3a.1: rate-limit for trigger-on-change DV announces — max 2 triggered updates per
      //   60 s per node (Sim: >= 2 ticks/60 s). Overflow stays "dirty" and fires in the next free slot.
      bb_trigger_limiter(2, 60)
#if defined(WITH_RS232_BRIDGE)
      , bridge(&_prefs, WITH_RS232_BRIDGE, _mgr, &rtc)
#endif
#if defined(WITH_ESPNOW_BRIDGE)
      , bridge(&_prefs, _mgr, &rtc)
#endif
{
  last_millis = 0;
  uptime_millis = 0;
  next_local_advert = next_flood_advert = 0;
  dirty_contacts_expiry = 0;
  set_radio_at = revert_radio_at = 0;
  _logging = false;
  region_load_active = false;

#if MAX_NEIGHBOURS
  memset(neighbours, 0, sizeof(neighbours));
#endif

  // MHR Stufe B: clear suppression tables (fixed allocation, populated passively at runtime)
  memset(supp_pending, 0, sizeof(supp_pending));
  memset(supp_twohop, 0, sizeof(supp_twohop));

  // MHR Phase 2: backbone tables are cleared by Backbone's own ctor; timer not scheduled until begin()
  //   AND only while bb_enable=1. With bb_enable=0 the backbone is never armed -> inert.
  next_bb_announce = 0;

  // defaults
  memset(&_prefs, 0, sizeof(_prefs));
  _prefs.airtime_factor = 1.0;
  _prefs.rx_delay_base = 10.0f;  // MHR: SNR-weighted flood rebroadcast ON by default (revert at runtime via CLI: set rxdelay 0)
  _prefs.tx_delay_factor = 0.5f; // was 0.25f
  _prefs.direct_tx_delay_factor = 0.3f; // was 0.2
  _prefs.tx_snr_weight = 0.5f;   // MHR: secondary lever — bias strong-SNR receptions earlier (revert: set txsnrweight 0)
  _prefs.tx_hop_weight = 0.6f;   // MHR: PRIMARY lever — fewer-hop copies rebroadcast earlier (revert: set txhopweight 0)
  // MHR Stufe B: redundancy-guarded flood suppression. DEFAULT OFF (supp_enable=0) => behaviour EXACTLY
  //   as Stufe A. Conservative validated guard defaults (docs/MHR/study/SUPPRESSION_VALIDATION.md).
  //   Enable per CLI only after bench validation: set supp.enable 1.
  _prefs.supp_enable = 0;        // OFF by default — feature dormant
  _prefs.supp_min_degree = 4;    // G1: never silence below 4 known neighbours (hard-spec default; study sweet-spot allows 3)
  _prefs.supp_k_cover = 2;       // G2: need >=2 distinct qualified cover senders
  _prefs.supp_snr_floor = -6;    // G4: cover senders must have EWMA-SNR >= -6 dB
  _prefs.supp_prob = 80;         // G5: suppress with 80% probability (a fraction always still sends)
  // MHR Best-of-N at destination: DEFAULT ON. Safe because the collection window only changes WHICH
  //   reciprocal path is returned (fewest hops, then best SNR), never whether/how often the payload is
  //   delivered (that stays exactly-once on the first copy). With one heard copy it is identical to
  //   first-wins. Reversible per CLI: set bofn.enable 0.
  _prefs.bofn_enable = 1;        // ON by default
  _prefs.bofn_window_ms = 1500;  // collection window (~1-2x typical multi-hop flood spread); set bofn.window <ms>
  // MHR Phase 2: proactive region backbone (DV control-plane). DEFAULT OFF (bb_enable=0) => fully inert:
  //   no DV sent, no DV processed, routing path bit-identical to today. Enable per CLI only after bench
  //   validation: set bb.enable 1. Period floored at 300 s (Design §3). See Phase2_Backbone_Design.md.
  _prefs.bb_enable = 0;          // OFF by default — feature dormant
  _prefs.bb_period_s = 600;      // periodic DV announce interval (Design default 600 s, min 300)
  _prefs.bb_holddown_s = 1200;   // hold-down after a route loss/poison (~2 announce periods)
  StrHelper::strncpy(_prefs.node_name, ADVERT_NAME, sizeof(_prefs.node_name));
  _prefs.node_lat = ADVERT_LAT;
  _prefs.node_lon = ADVERT_LON;
  StrHelper::strncpy(_prefs.password, ADMIN_PASSWORD, sizeof(_prefs.password));
  _prefs.freq = LORA_FREQ;
  _prefs.sf = LORA_SF;
  _prefs.bw = LORA_BW;
  _prefs.cr = LORA_CR;
  _prefs.tx_power_dbm = LORA_TX_POWER;
  _prefs.advert_interval = 1;        // default to 2 minutes for NEW installs
  _prefs.flood_advert_interval = 12; // 12 hours
  // MHR: _prefs.flood_max is now the HARD user CEILING for the adaptive flood-hop limit (see
  //      mhrEffectiveFloodMax()). The effective working cap floats between MHR_FLOOD_MAX_FLOOR (>= the
  //      measured P90=18) and this ceiling, tracking the observed diameter — so legitimate long paths
  //      are no longer cut the way the old fixed 15 did, while far detours are still bounded. Purely
  //      LOCAL (allowPacketForward); stock nodes (64) are unaffected. Override the ceiling: set flood.max <n>.
  _prefs.flood_max = 32;
  _mhr_obs_diam = 0;          // MHR: no diameter observed yet -> effective cap starts at the safe floor
  _mhr_diam_decay_at = 0;     // MHR: decay timer arms on first use
  _prefs.interference_threshold = 0; // disabled

  // bridge defaults
  _prefs.bridge_enabled = 1;    // enabled
  _prefs.bridge_delay   = 500;  // milliseconds
  _prefs.bridge_pkt_src = 0;    // logTx
  _prefs.bridge_baud = 115200;  // baud rate
  _prefs.bridge_channel = 1;    // channel 1

  StrHelper::strncpy(_prefs.bridge_secret, "LVSITANOS", sizeof(_prefs.bridge_secret));

  // GPS defaults
  _prefs.gps_enabled = 0;
  _prefs.gps_interval = 0;
  _prefs.advert_loc_policy = ADVERT_LOC_PREFS;

  _prefs.adc_multiplier = 0.0f; // 0.0f means use default board multiplier

#if defined(USE_SX1262) || defined(USE_SX1268)
#ifdef SX126X_RX_BOOSTED_GAIN
  _prefs.rx_boosted_gain = SX126X_RX_BOOSTED_GAIN;
#else
  _prefs.rx_boosted_gain = 1; // enabled by default;
#endif
#endif

  pending_discover_tag = 0;
  pending_discover_until = 0;

  memset(default_scope.key, 0, sizeof(default_scope.key));
}

void MyMesh::begin(FILESYSTEM *fs) {
  mesh::Mesh::begin();
  _fs = fs;
  // load persisted prefs
  _cli.loadPrefs(_fs);
  // MHR: wire Best-of-N path discovery from prefs into the Mesh base machinery.
  setBestOfN(_prefs.bofn_enable != 0, _prefs.bofn_window_ms);
  // MHR Phase 2: arm the periodic DV announce ONLY when the backbone is enabled. While bb_enable=0 the
  //   timer stays 0 and is never serviced -> no DV is ever sent (fully inert). First announce is offset
  //   by one period so it never races the boot advert.
  if (_prefs.bb_enable) {
    next_bb_announce = futureMillis((uint32_t)_prefs.bb_period_s * 1000UL);
  }
  acl.load(_fs, self_id);
  // TODO: key_store.begin();
  region_map.load(_fs);

  // establish default-scope
  {
    RegionEntry* r = region_map.getDefaultRegion();
    if (r) {
      region_map.getTransportKeysFor(*r, &default_scope, 1);
    } else {
#ifdef DEFAULT_FLOOD_SCOPE_NAME
      r = region_map.findByName(DEFAULT_FLOOD_SCOPE_NAME);
      if (r == NULL) {
        r = region_map.putRegion(DEFAULT_FLOOD_SCOPE_NAME, 0);  // auto-create the default scope region
        if (r) { r->flags = 0; }   // Allow-flood
      }
      if (r) {
        region_map.setDefaultRegion(r);
        region_map.getTransportKeysFor(*r, &default_scope, 1);
      }
#endif
    }
  }

#if defined(WITH_BRIDGE)
  if (_prefs.bridge_enabled) {
    bridge.begin();
  }
#endif

  radio_set_params(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr);
  radio_set_tx_power(_prefs.tx_power_dbm);

  radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
  MESH_DEBUG_PRINTLN("RX Boosted Gain Mode: %s",
                     radio_driver.getRxBoostedGainMode() ? "Enabled" : "Disabled");

  updateAdvertTimer();
  updateFloodAdvertTimer();

  board.setAdcMultiplier(_prefs.adc_multiplier);

#if ENV_INCLUDE_GPS == 1
  applyGpsPrefs();
#endif
}

void MyMesh::sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis, uint8_t path_hash_size) {
  if (scope.isNull()) {
    sendFlood(pkt, delay_millis, path_hash_size);
  } else {
    uint16_t codes[2];
    codes[0] = scope.calcTransportCode(pkt);
    codes[1] = 0;  // REVISIT: set to 'home' Region, for sender/return region?
    sendFlood(pkt, codes, delay_millis, path_hash_size);
  }
}

void MyMesh::applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) {
  set_radio_at = futureMillis(2000); // give CLI reply some time to be sent back, before applying temp radio params
  pending_freq = freq;
  pending_bw = bw;
  pending_sf = sf;
  pending_cr = cr;

  revert_radio_at = futureMillis(2000 + timeout_mins * 60 * 1000); // schedule when to revert radio params
}

bool MyMesh::formatFileSystem() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return InternalFS.format();
#elif defined(RP2040_PLATFORM)
  return LittleFS.format();
#elif defined(ESP32)
  return SPIFFS.format();
#else
#error "need to implement file system erase"
  return false;
#endif
}

void MyMesh::sendSelfAdvertisement(int delay_millis, bool flood) {
  mesh::Packet *pkt = createSelfAdvert();
  if (pkt) {
    if (flood) {
      sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);
    } else {
      sendZeroHop(pkt, delay_millis);
    }
  } else {
    MESH_DEBUG_PRINTLN("ERROR: unable to create advertisement packet!");
  }
}

void MyMesh::updateAdvertTimer() {
  if (_prefs.advert_interval > 0) { // schedule local advert timer
    next_local_advert = futureMillis(((uint32_t)_prefs.advert_interval) * 2 * 60 * 1000);
  } else {
    next_local_advert = 0; // stop the timer
  }
}

void MyMesh::updateFloodAdvertTimer() {
  if (_prefs.flood_advert_interval > 0) { // schedule flood advert timer
    next_flood_advert = futureMillis(((uint32_t)_prefs.flood_advert_interval) * 60 * 60 * 1000);
  } else {
    next_flood_advert = 0; // stop the timer
  }
}

void MyMesh::dumpLogFile() {
#if defined(RP2040_PLATFORM)
  File f = _fs->open(PACKET_LOG_FILE, "r");
#else
  File f = _fs->open(PACKET_LOG_FILE);
#endif
  if (f) {
    while (f.available()) {
      int c = f.read();
      if (c < 0) break;
      Serial.print((char)c);
    }
    f.close();
  }
}

void MyMesh::setTxPower(int8_t power_dbm) {
  radio_set_tx_power(power_dbm);
}

#if defined(USE_SX1262) || defined(USE_SX1268)
void MyMesh::setRxBoostedGain(bool enable) {
  radio_driver.setRxBoostedGainMode(enable);
}
#endif

void MyMesh::formatNeighborsReply(char *reply) {
  char *dp = reply;

#if MAX_NEIGHBOURS
  // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
  int16_t neighbours_count = 0;
  NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    auto neighbour = &neighbours[i];
    if (neighbour->heard_timestamp > 0) {
      sorted_neighbours[neighbours_count] = neighbour;
      neighbours_count++;
    }
  }

  // sort neighbours newest to oldest
  std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
    return a->heard_timestamp > b->heard_timestamp; // desc
  });

  for (int i = 0; i < neighbours_count && dp - reply < 134; i++) {
    NeighbourInfo *neighbour = sorted_neighbours[i];

    // add new line if not first item
    if (i > 0) *dp++ = '\n';

    char hex[10];
    // get 4 bytes of neighbour id as hex
    mesh::Utils::toHex(hex, neighbour->id.pub_key, 4);

    // add next neighbour
    uint32_t secs_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
    sprintf(dp, "%s:%d:%d", hex, secs_ago, neighbour->snr);
    while (*dp)
      dp++; // find end of string
  }
#endif
  if (dp == reply) { // no neighbours, need empty response
    strcpy(dp, "-none-");
    dp += 6;
  }
  *dp = 0; // null terminator
}

void MyMesh::removeNeighbor(const uint8_t *pubkey, int key_len) {
#if MAX_NEIGHBOURS
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    NeighbourInfo *neighbour = &neighbours[i];
    if (memcmp(neighbour->id.pub_key, pubkey, key_len) == 0) {
      neighbours[i] = NeighbourInfo(); // clear neighbour entry
    }
  }
#endif
}

void MyMesh::startRegionsLoad() {
  temp_map.resetFrom(region_map);   // rebuild regions in a temp instance
  memset(load_stack, 0, sizeof(load_stack));
  load_stack[0] = &temp_map.getWildcard();
  region_load_active = true;
}

bool MyMesh::saveRegions() {
  return region_map.save(_fs);
}

void MyMesh::onDefaultRegionChanged(const RegionEntry* r) {
  if (r) {
    region_map.getTransportKeysFor(*r, &default_scope, 1);
  } else {
    memset(default_scope.key, 0, sizeof(default_scope.key));
  }
}

void MyMesh::formatStatsReply(char *reply) {
  StatsFormatHelper::formatCoreStats(reply, board, *_ms, _err_flags, _mgr);
}

void MyMesh::formatRadioStatsReply(char *reply) {
  StatsFormatHelper::formatRadioStats(reply, _radio, radio_driver, getTotalAirTime(), getReceiveAirTime());
}

void MyMesh::formatPacketStatsReply(char *reply) {
  StatsFormatHelper::formatPacketStats(reply, radio_driver, getNumSentFlood(), getNumSentDirect(), 
                                       getNumRecvFlood(), getNumRecvDirect());
}

void MyMesh::saveIdentity(const mesh::LocalIdentity &new_id) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  IdentityStore store(*_fs, "");
#elif defined(ESP32)
  IdentityStore store(*_fs, "/identity");
#elif defined(RP2040_PLATFORM)
  IdentityStore store(*_fs, "/identity");
#else
#error "need to define saveIdentity()"
#endif
  store.save("_main", new_id);
}

void MyMesh::clearStats() {
  radio_driver.resetStats();
  resetStats();
  ((SimpleMeshTables *)getTables())->resetStats();
}

void MyMesh::handleCommand(uint32_t sender_timestamp, char *command, char *reply) {
  if (region_load_active) {
    if (StrHelper::isBlank(command)) {  // empty/blank line, signal to terminate 'load' operation
      region_map = temp_map;  // copy over the temp instance as new current map
      region_load_active = false;

      sprintf(reply, "OK - loaded %d regions", region_map.getCount());
    } else {
      char *np = command;
      while (*np == ' ') np++;   // skip indent
      int indent = np - command;

      char *ep = np;
      while (RegionMap::is_name_char(*ep)) ep++;
      if (*ep) { *ep++ = 0; }  // set null terminator for end of name

      while (*ep && *ep != 'F') ep++;  // look for (optional) flags

      if (indent > 0 && indent < 8 && strlen(np) > 0) {
        auto parent = load_stack[indent - 1];
        if (parent) {
          auto old = region_map.findByName(np);
          auto nw = temp_map.putRegion(np, parent->id, old ? old->id : 0);  // carry-over the current ID (if name already exists)
          if (nw) {
            nw->flags = old ? old->flags : (*ep == 'F' ? 0 : REGION_DENY_FLOOD);   // carry-over flags from curr

            load_stack[indent] = nw;  // keep pointers to parent regions, to resolve parent_id's
          }
        }
      }
      reply[0] = 0;
    }
    return;
  }

  while (*command == ' ') command++; // skip leading spaces

  if (strlen(command) > 4 && command[2] == '|') { // optional prefix (for companion radio CLI)
    memcpy(reply, command, 3);                    // reflect the prefix back
    reply += 3;
    command += 3;
  }

  // handle ACL related commands
  if (memcmp(command, "setperm ", 8) == 0) {   // format:  setperm {pubkey-hex} {permissions-int8}
    char* hex = &command[8];
    char* sp = strchr(hex, ' ');   // look for separator char
    if (sp == NULL) {
      strcpy(reply, "Err - bad params");
    } else {
      *sp++ = 0;   // replace space with null terminator

      uint8_t pubkey[PUB_KEY_SIZE];
      int hex_len = min(sp - hex, PUB_KEY_SIZE*2);
      if (mesh::Utils::fromHex(pubkey, hex_len / 2, hex)) {
        uint8_t perms = atoi(sp);
        if (acl.applyPermissions(self_id, pubkey, hex_len / 2, perms)) {
          dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);   // trigger acl.save()
          strcpy(reply, "OK");
        } else {
          strcpy(reply, "Err - invalid params");
        }
      } else {
        strcpy(reply, "Err - bad pubkey");
      }
    }
  } else if (sender_timestamp == 0 && strcmp(command, "get acl") == 0) {
    Serial.println("ACL:");
    for (int i = 0; i < acl.getNumClients(); i++) {
      auto c = acl.getClientByIdx(i);
      if (c->permissions == 0) continue;  // skip deleted (or guest) entries

      Serial.printf("%02X ", c->permissions);
      mesh::Utils::printHex(Serial, c->id.pub_key, PUB_KEY_SIZE);
      Serial.printf("\n");
    }
    reply[0] = 0;
  } else if (memcmp(command, "discover.neighbors", 18) == 0) {
    const char* sub = command + 18;
    while (*sub == ' ') sub++;
    if (*sub != 0) {
      strcpy(reply, "Err - discover.neighbors has no options");
    } else {
      sendNodeDiscoverReq();
      strcpy(reply, "OK - Discover sent");
    }
  } else{
    _cli.handleCommand(sender_timestamp, command, reply);  // common CLI commands
  }
}

void MyMesh::loop() {
#ifdef WITH_BRIDGE
  bridge.loop();
#endif

  mesh::Mesh::loop();

  // MHR: keep Best-of-N config in sync with prefs (so CLI `set bofn.*` applies without a reboot).
  setBestOfN(_prefs.bofn_enable != 0, _prefs.bofn_window_ms);

  // MHR Phase 2: proactive backbone control-plane. ENTIRELY gated on bb_enable — when off, this whole
  //   block is skipped and nothing about the send/receive path changes (bit-identical to today).
  if (_prefs.bb_enable) {
    // arm the timer the first time the feature is switched on at runtime (set bb.enable 1)
    if (next_bb_announce == 0) {
      next_bb_announce = futureMillis((uint32_t)_prefs.bb_period_s * 1000UL);
    }
    uint32_t now_secs = getRTCClock()->getCurrentTime();

    // periodic announce (Design §3: period >= 300 s, enforced by the pref constrain)
    if (millisHasNowPassed(next_bb_announce)) {
      bbSendUpdate(false);
      next_bb_announce = futureMillis((uint32_t)_prefs.bb_period_s * 1000UL);
    }

    // maintenance: expire stale neighbours, fail over to backups, poison + hold-down lost routes.
    //   A route loss schedules a triggered (rate-limited) retraction announce (§3a.1/§3a.2).
    if (_backbone.maintenance(now_secs, _prefs.bb_holddown_s)) {
      if (bb_trigger_limiter.allow(now_secs)) bbSendUpdate(true);
    }
    // flush any still-dirty (rate-limit-deferred) triggered changes when a slot frees up (§3a.1)
    else if (_backbone.hasDirty() && bb_trigger_limiter.allow(now_secs)) {
      bbSendUpdate(true);
    }
  } else {
    next_bb_announce = 0;   // feature off -> keep timer disarmed (re-arms cleanly if re-enabled)
  }

  if (next_flood_advert && millisHasNowPassed(next_flood_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    uint32_t delay_millis = 0;
    if (pkt) sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);

    updateFloodAdvertTimer(); // schedule next flood advert
    updateAdvertTimer();      // also schedule local advert (so they don't overlap)
  } else if (next_local_advert && millisHasNowPassed(next_local_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    if (pkt) sendZeroHop(pkt);

    updateAdvertTimer(); // schedule next local advert
  }

  if (set_radio_at && millisHasNowPassed(set_radio_at)) { // apply pending (temporary) radio params
    set_radio_at = 0;                                     // clear timer
    radio_set_params(pending_freq, pending_bw, pending_sf, pending_cr);
    MESH_DEBUG_PRINTLN("Temp radio params");
  }

  if (revert_radio_at && millisHasNowPassed(revert_radio_at)) { // revert radio params to orig
    revert_radio_at = 0;                                        // clear timer
    radio_set_params(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr);
    MESH_DEBUG_PRINTLN("Radio params restored");
  }

  // is pending dirty contacts write needed?
  if (dirty_contacts_expiry && millisHasNowPassed(dirty_contacts_expiry)) {
    acl.save(_fs);
    dirty_contacts_expiry = 0;
  }

  // update uptime
  uint32_t now = millis();
  uptime_millis += now - last_millis;
  last_millis = now;
}

// To check if there is pending work
bool MyMesh::hasPendingWork() const {
#if defined(WITH_BRIDGE)
  if (bridge.isRunning()) return true;  // bridge needs WiFi radio, can't sleep
#endif
  return _mgr->getOutboundTotal() > 0;
}
