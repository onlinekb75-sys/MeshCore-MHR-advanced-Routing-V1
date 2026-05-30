#include "Backbone.h"
#include <string.h>

// =====================================================================================================
//  MHR Phase 2 — Proactive Region Backbone (DV control-plane) — implementation.
//  See Backbone.h and docs/MHR/study/Phase2_Backbone_Design.md. This whole module is only ever reached
//  when bb_enable==1 (the MyMesh caller gates every entry point). With bb_enable==0 it is dead code.
// =====================================================================================================

Backbone::Backbone() {
  reset();
}

void Backbone::reset() {
  memset(_dests, 0, sizeof(_dests));
  memset(_neigh, 0, sizeof(_neigh));
  _next_seqno = 1;
}

// Per-link ETX-style cost from an EWMA-SNR (x4). Reliability-dominant (Design §3 metric, v2-H4): a strong
// link is ~base cost; a weak link costs progressively more. Unknown SNR (-128) => default base cost.
uint16_t Backbone::linkCostFromSnr(int snr_x4) {
  if (snr_x4 == -128) return BB_LINK_ETX_DEFAULT;  // unknown -> neutral base
  int snr_db = snr_x4 / 4;
  // Map SNR to a cost penalty: >= +6 dB -> base; each dB below adds ~5% up to a ceiling. Clamped.
  int penalty = (6 - snr_db) * 5;          // 0 at +6 dB, grows as link weakens
  if (penalty < 0) penalty = 0;
  if (penalty > 300) penalty = 300;        // ceiling: a very weak link is ~4x base cost
  int cost = BB_LINK_ETX_DEFAULT + (BB_LINK_ETX_DEFAULT * penalty) / 100;
  if (cost > (BB_METRIC_INF - 1)) cost = BB_METRIC_INF - 1;
  return (uint16_t)cost;
}

// ---------------------------------------------------------------------------------------------------
//  Neighbour table
// ---------------------------------------------------------------------------------------------------
BBNeighbour* Backbone::findNeighbour(const uint8_t* hash) {
  for (int i = 0; i < BB_MAX_NEIGHBOURS; i++) {
    if (_neigh[i].heard_secs != 0 && memcmp(_neigh[i].hash, hash, BB_HASH_LEN) == 0) return &_neigh[i];
  }
  return NULL;
}
const BBNeighbour* Backbone::findNeighbour(const uint8_t* hash) const {
  for (int i = 0; i < BB_MAX_NEIGHBOURS; i++) {
    if (_neigh[i].heard_secs != 0 && memcmp(_neigh[i].hash, hash, BB_HASH_LEN) == 0) return &_neigh[i];
  }
  return NULL;
}

// Originate our own reachability so the control-plane has something to converge on. A self-row has
//   cost 0, no next-hop (the route IS us), and is_self=true so maintenance never poisons it.
void Backbone::originateSelf(const uint8_t* self_hash, uint16_t self_region, bool is_border, uint32_t now_secs) {
  // MHR B1: we are the ORIGIN/seqno-owner of every self route. Advance our origin generation ONCE per
  //   call and stamp it on all self-owned dests. This is the ONLY place a self-owned dest's seqno moves
  //   forward; relayers propagate a dest's seqno verbatim (Design §3 "pro Ziel Seqno" / §3a.3).
  _next_seqno++;
  if (_next_seqno == 0) _next_seqno = 1;   // skip 0 (0 means "no generation yet" on a freshly-alloc'd row)

  // self repeater route (intra-region reachability of THIS repeater)
  BBDest* r = allocDest(BB_DEST_REPEATER, self_hash, now_secs);
  r->is_self = true;
  r->has_next = false;          // we are the origin; receivers add their link cost
  r->has_backup = false;
  r->metric = 0;                // advertised cost 0
  r->fd = 0;
  r->seqno = _next_seqno;       // MHR B1: origin advances this dest's per-dest generation
  r->updated_secs = now_secs;

  // border router also originates the cost-0 aggregate for its home region (H1 inter-region, Design §3a.3)
  if (is_border && self_region != 0) {
    uint8_t rid[BB_HASH_LEN];
    rid[0] = (uint8_t)(self_region & 0xFF);
    if (BB_HASH_LEN > 1) rid[1] = (uint8_t)((self_region >> 8) & 0xFF);
    BBDest* a = allocDest(BB_DEST_REGION, rid, now_secs);
    a->is_self = true;
    a->has_next = false;
    a->has_backup = false;
    a->metric = 0;
    a->fd = 0;
    a->seqno = _next_seqno;     // MHR B1: ABR is the seqno-owner of its home-region aggregate
    a->updated_secs = now_secs;
  }
}

void Backbone::putNeighbour(const uint8_t* hash, int snr_x4, uint32_t now_secs) {
  BBNeighbour* nb = findNeighbour(hash);
  if (nb == NULL) {
    // find empty / least-recently-heard slot
    BBNeighbour* lru = &_neigh[0];
    uint32_t oldest = 0xFFFFFFFF;
    for (int i = 0; i < BB_MAX_NEIGHBOURS; i++) {
      if (_neigh[i].heard_secs == 0) { lru = &_neigh[i]; break; }
      if (_neigh[i].heard_secs < oldest) { oldest = _neigh[i].heard_secs; lru = &_neigh[i]; }
    }
    nb = lru;
    memset(nb, 0, sizeof(*nb));
    memcpy(nb->hash, hash, BB_HASH_LEN);
    nb->snr = (int8_t)((snr_x4 < -128 || snr_x4 > 127) ? -128 : snr_x4);
  } else {
    // EWMA-smooth the link SNR (alpha = 1/4), like putNeighbour() in MyMesh (stable link sensing, L0).
    if (snr_x4 != -128) {
      nb->snr = (nb->snr == -128) ? (int8_t)snr_x4 : (int8_t)(((int)nb->snr * 3 + snr_x4) / 4);
    }
  }
  nb->heard_secs = now_secs;
}

// ---------------------------------------------------------------------------------------------------
//  Destination (DV) table
// ---------------------------------------------------------------------------------------------------
BBDest* Backbone::findDest(uint8_t kind, const uint8_t* id) {
  for (int i = 0; i < BB_MAX_DESTS; i++) {
    if (_dests[i].updated_secs != 0 && _dests[i].kind == kind
        && memcmp(_dests[i].id, id, BB_HASH_LEN) == 0) return &_dests[i];
  }
  return NULL;
}
const BBDest* Backbone::findDest(uint8_t kind, const uint8_t* id) const {
  for (int i = 0; i < BB_MAX_DESTS; i++) {
    if (_dests[i].updated_secs != 0 && _dests[i].kind == kind
        && memcmp(_dests[i].id, id, BB_HASH_LEN) == 0) return &_dests[i];
  }
  return NULL;
}

// Find-or-allocate a dest row. Eviction policy: least-stable first = the row with no live next-hop and
// the oldest update; otherwise the globally oldest row (Design §5 / project rule: least-stable eviction).
BBDest* Backbone::allocDest(uint8_t kind, const uint8_t* id, uint32_t now_secs) {
  BBDest* row = findDest(kind, id);
  if (row) return row;

  BBDest* victim = NULL;
  uint32_t oldest_dead = 0xFFFFFFFF;   // best (oldest) candidate among rows WITHOUT a live next-hop
  uint32_t oldest_any = 0xFFFFFFFF;    // fallback: globally oldest
  BBDest* oldest_any_row = &_dests[0];
  for (int i = 0; i < BB_MAX_DESTS; i++) {
    BBDest* r = &_dests[i];
    if (r->updated_secs == 0) { victim = r; break; }     // free slot
    if (!r->has_next && r->updated_secs < oldest_dead) { oldest_dead = r->updated_secs; victim = r; }
    if (r->updated_secs < oldest_any) { oldest_any = r->updated_secs; oldest_any_row = r; }
  }
  if (victim == NULL) victim = oldest_any_row;           // all rows have live next-hops -> evict oldest
  memset(victim, 0, sizeof(*victim));
  victim->kind = kind;
  memcpy(victim->id, id, BB_HASH_LEN);
  victim->fd = BB_METRIC_INF;       // no feasible distance achieved yet
  victim->metric = BB_METRIC_INF;
  victim->backup_metric = BB_METRIC_INF;
  victim->updated_secs = now_secs;
  return victim;
}

// ---------------------------------------------------------------------------------------------------
//  DV receive — Babel-feasibility + seqno + feasible-successor + hold-down (Design §3, §3a)
// ---------------------------------------------------------------------------------------------------
bool Backbone::onDVReceived(const mesh::Packet* pkt, const uint8_t* self_hash, int rx_snr_x4, uint32_t now_secs,
                            uint16_t holddown_s) {
  const uint8_t* p = pkt->payload;
  uint16_t len = pkt->payload_len;
  if (len < BB_HDR_LEN) return false;
  if ((p[0] & 0x0F) != DV_FMT_VER) return false;     // unknown format version -> ignore (forward-compat)

  // MHR B1: header no longer carries a packet-level seqno. Seqno is now per-entry (per-dest).
  uint8_t sender[BB_HASH_LEN];
  memcpy(sender, &p[2], BB_HASH_LEN);
  uint8_t n = p[4];

  if (memcmp(sender, self_hash, BB_HASH_LEN) == 0) return false;  // our own echo -> ignore

  // sanity: declared entry count must fit the packet
  if ((uint16_t)BB_HDR_LEN + (uint16_t)n * BB_ENTRY_SIZE > len) return false;

  // refresh the sender as a DV neighbour (it is a valid candidate next-hop now)
  putNeighbour(sender, rx_snr_x4, now_secs);
  uint16_t link_cost = linkCostFromSnr(rx_snr_x4);

  bool self_changed = false;
  const uint8_t* e = &p[BB_HDR_LEN];
  for (uint8_t i = 0; i < n; i++, e += BB_ENTRY_SIZE) {
    uint8_t  kind = e[0];
    if (kind != BB_DEST_REPEATER && kind != BB_DEST_REGION) continue;  // ignore unknown dest kinds
    const uint8_t* dest_id = &e[1];
    uint16_t adv_metric, adv_fd, entry_seqno;
    memcpy(&adv_metric, &e[3], 2);
    memcpy(&adv_fd, &e[5], 2);
    memcpy(&entry_seqno, &e[7], 2);    // MHR B1: PER-DEST seqno owned by this route's origin

    // ignore an entry that points back at us (kind==repeater, dest==self) — we are the origin
    if (kind == BB_DEST_REPEATER && memcmp(dest_id, self_hash, BB_HASH_LEN) == 0) continue;

    // candidate cost via this sender = its advertised metric + our link cost to it (saturating)
    uint32_t cand32 = (adv_metric >= BB_METRIC_INF) ? BB_METRIC_INF
                                                    : (uint32_t)adv_metric + link_cost;
    uint16_t cand = (cand32 >= BB_METRIC_INF) ? BB_METRIC_INF : (uint16_t)cand32;

    BBDest* row = allocDest(kind, dest_id, now_secs);

    // never let an incoming entry alter a self-originated route (we are the cost-0 origin for it).
    //   For region aggregates this is the Babel "one seqno-owner per dest" guard (Design §3a.3): our
    //   own home-region aggregate at cost 0 can never be displaced by another ABR's view.
    if (row->is_self) continue;

    // ---- POISON: sender retracts this dest (INF). If it was our current primary next-hop, drop it
    //      and fall to the backup if still feasible; else mark the dest lost + start hold-down. ----
    if (adv_metric >= BB_METRIC_INF) {
      bool from_primary = row->has_next && memcmp(row->next_hop, sender, BB_HASH_LEN) == 0;
      bool from_backup  = row->has_backup && memcmp(row->backup_hop, sender, BB_HASH_LEN) == 0;
      if (from_backup) { row->has_backup = false; row->backup_metric = BB_METRIC_INF; }
      if (from_primary) {
        if (row->has_backup) {
          // promote backup -> primary (no re-flood needed, Design §3 H3).
          // MHR W2: the backup was stored only when STRICTLY FD-feasible (cand < row->fd at set-time, see
          //   the backup-set guards below), so this promotion is provably loop-free WITHOUT a recheck.
          memcpy(row->next_hop, row->backup_hop, BB_HASH_LEN);
          row->metric = row->backup_metric;
          row->has_backup = false; row->backup_metric = BB_METRIC_INF;
          row->seqno = (uint16_t)(row->seqno + 1);
          row->dirty = true; self_changed = true;
        } else {
          // route lost with no backup -> poison + hold-down (Design §3a.2)
          row->has_next = false;
          row->metric = BB_METRIC_INF;
          row->seqno = (uint16_t)(row->seqno + 1);          // bump seqno so our retraction overrides stale
          row->holddown_until = now_secs + holddown_s;      // reject worse alternatives during re-converge
          row->dirty = true; self_changed = true;
        }
      }
      row->updated_secs = now_secs;
      continue;
    }

    // ---- hold-down: while active for this dest, reject any WORSE-or-equal alternative (Design §3a.2)
    //      to avoid prematurely accepting a loop route during re-convergence. ----
    bool in_holddown = (row->holddown_until > now_secs);

    // ---- Babel feasibility (Design §3 / §3a.3): a candidate may become next-hop ONLY if its cost is
    //      STRICTLY LESS than the feasible distance we have achieved for THIS dest (origin-independent).
    //      Equal/greater is rejected -> loop-free during convergence. A newer seqno relaxes the FD
    //      (the ORIGIN advanced its generation), which is what lets the network re-converge upward. ----
    //  MHR B1: compare the ENTRY's PER-DEST seqno (owned/advanced ONLY by this route's origin) against
    //    the generation we last accepted for this dest. With the old per-announcer header seqno this was
    //    bumped on every announce, so `seqno_newer` was almost always true and the OR-clause bypassed the
    //    feasibility gate, making strictly-worse loop routes feasible. Now `seqno_newer` truly means "the
    //    origin advanced this dest's generation"; a routine relayed re-announce carries the SAME seqno, so
    //    it must pass strict `cand < fd` (real feasibility) to be adopted.
    bool seqno_newer = (int16_t)(entry_seqno - row->seqno) > 0;
    bool feasible = (cand < row->fd) || (seqno_newer && !in_holddown);

    if (row->has_next && memcmp(row->next_hop, sender, BB_HASH_LEN) == 0) {
      // update from our CURRENT primary next-hop: always track its cost (it owns the route). On a newer
      // seqno reset FD to the new metric (origin advanced its generation); otherwise only relax FD
      // downward (never inflate fd on a worsening — that would wrongly reject good backups).
      // MHR W1: only flag self_changed when our advertised cost actually MOVED — a routine re-announce
      //   that keeps the same metric must NOT trigger a re-announce (avoids the triggered-announce storm).
      bool metric_moved = (cand != row->metric);
      row->metric = cand;
      if (seqno_newer) { row->fd = cand; row->seqno = entry_seqno; }   // MHR B1: adopt origin's per-dest seqno
      else if (cand < row->fd) row->fd = cand;
      row->updated_secs = now_secs;
      if (metric_moved) self_changed = true;
      continue;
    }

    if (!feasible) {
      // not feasible as primary — but it may still serve as a feasible-successor BACKUP if strictly
      // better than any current backup AND strictly less than fd (pre-validated loop-free, Design §3 H3).
      if (cand < row->fd && cand < row->backup_metric && !in_holddown) {
        memcpy(row->backup_hop, sender, BB_HASH_LEN);
        row->backup_metric = cand;
        row->has_backup = true;
        row->updated_secs = now_secs;
      }
      continue;
    }

    // feasible candidate. Adopt as primary if we have none, or with hysteresis vs the current primary
    // (>= ~15% improvement, Design §3) to prevent flapping.
    bool adopt = false;
    if (!row->has_next) {
      adopt = true;
    } else {
      uint32_t threshold = (uint32_t)row->metric * (100 - BB_HYSTERESIS_PCT) / 100;
      if (cand < threshold) adopt = true;
    }
    if (adopt) {
      // demote the old primary to backup if it is still better than the existing backup.
      // MHR W2: only keep it as a backup if it is still STRICTLY FD-feasible (row->metric < row->fd).
      //   The old primary's metric may have inflated above fd via cost-tracking from its next-hop; an
      //   inflated (non-feasible) hop must not become a backup that later gets promoted unchecked.
      if (row->has_next && row->metric < row->fd && row->metric < row->backup_metric) {
        memcpy(row->backup_hop, row->next_hop, BB_HASH_LEN);
        row->backup_metric = row->metric;
        row->has_backup = true;
      }
      memcpy(row->next_hop, sender, BB_HASH_LEN);
      row->metric = cand;
      // Babel FD update: on a NEWER seqno the origin advanced its generation -> reset FD to this metric
      //   (the old generation's FD no longer constrains us). Otherwise only ever relax FD downward.
      // MHR B1: adopt the ENTRY's per-dest seqno (the origin's generation), not a per-announcer counter.
      //   row->seqno==0 means a freshly-allocated row with no generation yet -> take the origin's seqno.
      if (seqno_newer || row->seqno == 0) { row->fd = cand; row->seqno = entry_seqno; }
      else if (cand < row->fd) row->fd = cand;
      row->has_next = true;
      row->updated_secs = now_secs;
      self_changed = true;
    } else {
      // feasible but not enough improvement -> keep as backup if better than current backup.
      // MHR W2: require STRICT FD-feasibility (cand < row->fd) for a backup, not merely "feasible" —
      //   a candidate can be `feasible` here purely via `seqno_newer` (a fresher origin generation)
      //   WITHOUT cand < fd. Such a hop is a valid PRIMARY (it advanced the generation) but is NOT a
      //   pre-validated feasible successor, so it must not be stored as a backup that maintenance() or
      //   the poison path would later promote UNCHECKED (that promotion does no recheck — see there).
      //   Storing only cand < fd backups makes every backup promotion provably loop-free.
      if (cand < row->fd && cand < row->backup_metric) {
        memcpy(row->backup_hop, sender, BB_HASH_LEN);
        row->backup_metric = cand;
        row->has_backup = true;
        row->updated_secs = now_secs;
      }
    }
  }
  return self_changed;
}

// ---------------------------------------------------------------------------------------------------
//  Maintenance — expire stale neighbours, invalidate routes via dead next-hops, apply hold-down.
//  Returns true if any route was lost (caller may schedule a triggered poison announce).
// ---------------------------------------------------------------------------------------------------
bool Backbone::maintenance(uint32_t now_secs, uint16_t holddown_s) {
  // 1) expire stale neighbours
  for (int i = 0; i < BB_MAX_NEIGHBOURS; i++) {
    if (_neigh[i].heard_secs == 0) continue;
    if (now_secs - _neigh[i].heard_secs > BB_NEIGHBOUR_STALE_S) {
      memset(&_neigh[i], 0, sizeof(_neigh[i]));
    }
  }

  bool any_lost = false;
  // 2) invalidate dest routes whose primary/backup next-hop is no longer a live neighbour
  for (int i = 0; i < BB_MAX_DESTS; i++) {
    BBDest* r = &_dests[i];
    if (r->updated_secs == 0) continue;
    if (r->is_self) continue;     // self-originated routes are never poisoned/expired by next-hop death

    if (r->has_backup && findNeighbour(r->backup_hop) == NULL) {
      r->has_backup = false; r->backup_metric = BB_METRIC_INF;
    }
    if (r->has_next && findNeighbour(r->next_hop) == NULL) {
      // primary next-hop died
      if (r->has_backup) {
        // MHR W2: backups are only ever stored when STRICTLY FD-feasible (cand < fd at set-time in
        //   onDVReceived), so promoting one here is provably loop-free without a recheck.
        memcpy(r->next_hop, r->backup_hop, BB_HASH_LEN);
        r->metric = r->backup_metric;
        r->has_backup = false; r->backup_metric = BB_METRIC_INF;
        r->seqno = (uint16_t)(r->seqno + 1);
        r->dirty = true;
      } else {
        // lost with no backup -> poison + hold-down (Design §3a.2)
        r->has_next = false;
        r->metric = BB_METRIC_INF;
        r->seqno = (uint16_t)(r->seqno + 1);
        r->holddown_until = now_secs + holddown_s;   // reject worse alternatives during re-convergence
        r->dirty = true;
        any_lost = true;
      }
    }
  }
  return any_lost;
}

// ---------------------------------------------------------------------------------------------------
//  DV transmit — pack the entries we advertise (incl. poisoned/INF withdrawals) into one zero-hop pkt.
// ---------------------------------------------------------------------------------------------------
int Backbone::buildDVPayload(uint8_t* out, const uint8_t* self_hash, bool is_border) {
  // MHR B1: header carries NO packet-level seqno anymore (seqno is per-entry/per-dest below).
  out[0] = DV_FMT_VER;
  out[1] = is_border ? BB_FLAG_BORDER : 0;
  memcpy(&out[2], self_hash, BB_HASH_LEN);

  uint8_t n = 0;
  uint8_t* e = &out[BB_HDR_LEN];
  for (int i = 0; i < BB_MAX_DESTS && n < BB_MAX_TX_ENTRIES; i++) {
    BBDest* r = &_dests[i];
    if (r->updated_secs == 0) continue;

    // advertise: live routes with their metric; lost routes (no next-hop) as poison (INF) so the
    // retraction overrides stale aggregates at receivers (Design §3a.2 split-horizon-free poisoning).
    uint16_t adv_metric = r->is_self ? 0 : (r->has_next ? r->metric : BB_METRIC_INF);
    uint16_t adv_fd = r->fd;
    // MHR B1: advertise the route's PER-DEST seqno. The ORIGIN advanced it in originateSelf(); a relayer
    //   sends row->seqno UNCHANGED (we never substitute our own announce counter). This is what makes
    //   `seqno_newer` at the receiver mean "the origin advanced this dest's generation", restoring the
    //   "one seqno-owner per dest" Babel invariant (Design §3 / §3a.3).
    uint16_t adv_seqno = r->seqno;

    e[0] = r->kind;
    memcpy(&e[1], r->id, BB_HASH_LEN);
    memcpy(&e[3], &adv_metric, 2);
    memcpy(&e[5], &adv_fd, 2);
    memcpy(&e[7], &adv_seqno, 2);
    e += BB_ENTRY_SIZE;
    n++;
  }
  out[BB_HDR_LEN - 1] = n;   // n is the last header byte (payload[4])
  if (n == 0) return 0;      // nothing to advertise
  return BB_HDR_LEN + n * BB_ENTRY_SIZE;
}

// ---------------------------------------------------------------------------------------------------
//  Route lookup for the data-path short-circuit. NEVER returns a route unless it is strictly better
//  than the supplied flood_cost (so the caller's fallback to flood-and-cache is never worse).
// ---------------------------------------------------------------------------------------------------
BBRoute Backbone::lookupRoute(uint8_t kind, const uint8_t* dest_id, uint16_t flood_cost) const {
  BBRoute r;
  r.found = false; r.metric = BB_METRIC_INF;
  memset(r.next_hop, 0, BB_HASH_LEN);

  const BBDest* d = findDest(kind, dest_id);
  if (d == NULL || !d->has_next || d->metric >= BB_METRIC_INF) return r;   // no live route
  if (d->metric >= flood_cost) return r;                                   // not better -> fall back to flood

  r.found = true;
  r.metric = d->metric;
  memcpy(r.next_hop, d->next_hop, BB_HASH_LEN);
  return r;
}

bool Backbone::hasDirty() const {
  for (int i = 0; i < BB_MAX_DESTS; i++) {
    if (_dests[i].updated_secs != 0 && _dests[i].dirty) return true;
  }
  return false;
}

void Backbone::clearDirty() {
  for (int i = 0; i < BB_MAX_DESTS; i++) _dests[i].dirty = false;
}

int Backbone::countDests() const {
  int n = 0;
  for (int i = 0; i < BB_MAX_DESTS; i++) if (_dests[i].updated_secs != 0) n++;
  return n;
}
int Backbone::countNeighbours() const {
  int n = 0;
  for (int i = 0; i < BB_MAX_NEIGHBOURS; i++) if (_neigh[i].heard_secs != 0) n++;
  return n;
}
