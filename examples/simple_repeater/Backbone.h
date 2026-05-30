#pragma once

#include <Arduino.h>
#include <Packet.h>
#include <Identity.h>

// =====================================================================================================
//  MHR Phase 2 — Proactive Region Backbone (Distance-Vector control-plane).
//
//  This module is a self-contained, fixed-size, allocation-free implementation of the proactive
//  distance-vector (DV) control-plane specified in docs/MHR/study/Phase2_Backbone_Design.md, including
//  the §3a churn-hardening (trigger-on-change rate-limited, hold-down + route poisoning, and the
//  origin-independent aggregate feasible-distance Babel invariant).
//
//  DESIGN-OFF = INERT: every entry point is gated by the caller on _prefs.bb_enable. When bb_enable==0
//  NONE of these methods are ever called, so the firmware send/receive path is bit-identical to today.
//  When bb_enable==1 the only on-air effect is the periodic/triggered ZERO-HOP DV packet
//  (PAYLOAD_TYPE_DV) which stock nodes ignore (Mesh::onRecvPacket default case -> discard, no reflood).
//
//  WIRE FORMAT (zero-hop, ignorable; Design §2):
//    payload[0]      = DV_FMT_VER (format version + flag bits)
//    payload[1]      = node role/flags (bit BB_FLAG_BORDER => sender is an ABR announcing aggregates)
//    payload[2..3]   = sender hash (BB_HASH_LEN bytes — the next-hop identity; the zero-hop packet
//                      carries no path, so the next-hop must be named in the payload)
//    payload[4]      = n  (number of entries that follow)
//    then n entries, each BB_ENTRY_SIZE bytes:
//      [0]      dest_kind  (BB_DEST_REPEATER | BB_DEST_REGION)
//      [1..2]   dest_id    (repeater hash prefix, or region id)
//      [3..4]   metric     (u16 ETX-style cost; BB_METRIC_INF = poison/withdraw)
//      [5..6]   fd         (u16 feasible distance the sender has itself achieved for this dest)
//      [7..8]   seqno      (u16 PER-DEST generation owned by the route's ORIGIN — Design §3/§3a.3)
//
//  MHR B1 FIX: the seqno is PER DESTINATION, NOT per packet/announcer. Only a route's ORIGIN (the
//    cost-0 self route in originateSelf(); for aggregates the ABR for its home region) advances that
//    dest's seqno; a relayer propagates the route's seqno UNCHANGED. The old layout carried ONE
//    announcer seqno in the header that was (mis)used as every dest's "generation"; because that
//    counter bumped on every announce, `seqno_newer` was almost always true and silently bypassed the
//    Babel feasibility gate, making strictly-worse loop routes feasible. Per-dest seqno restores the
//    "one seqno-owner per dest" invariant (Design §3 / §3a.3) — the core loop-breaker. The header no
//    longer carries a packet-level seqno.
//
//  All comments DE/EN as in the surrounding modules.
// =====================================================================================================

// ---- fixed table dimensions (RAM budget, Design §5 ~1-3 KB) ----
#ifndef BB_MAX_DESTS            // distance-vector table rows (one per known dest repeater + region aggregate)
  #define BB_MAX_DESTS         40
#endif
#ifndef BB_MAX_NEIGHBOURS      // tracked DV neighbours (direct repeaters we have heard DV from)
  #define BB_MAX_NEIGHBOURS    16
#endif
#ifndef BB_HASH_LEN            // bytes of a node hash we key on (2 bytes = the network's real path hash size)
  #define BB_HASH_LEN           2
#endif
#ifndef BB_MAX_TX_ENTRIES      // max DV entries we pack into one outgoing zero-hop packet
  // MHR B1: entries grew to 9 bytes (added per-dest seqno). With BB_HDR_LEN=5 and MAX_PACKET_PAYLOAD=184
  //   we cap at 19 entries -> 5 + 19*9 = 176 bytes <= 184 (packet-size guarantee, see (d) in report).
  #define BB_MAX_TX_ENTRIES    19
#endif
#ifndef BB_NEIGHBOUR_STALE_S   // a DV neighbour not heard from within this many secs is considered dead
  #define BB_NEIGHBOUR_STALE_S 1800
#endif

// ---- wire constants ----
#define DV_FMT_VER            0x01     // format version 1 (low nibble); upper bits reserved
#define BB_FLAG_BORDER        0x01     // sender is an Area Border Router (announces region aggregates)
#define BB_DEST_REPEATER      0x00     // dest_id is a 2-byte repeater hash prefix (intra-region)
#define BB_DEST_REGION        0x01     // dest_id is a 2-byte region id (inter-region aggregate)
#define BB_ENTRY_SIZE         9        // MHR B1: 1 (kind) + 2 (id) + 2 (metric) + 2 (fd) + 2 (per-dest seqno)
#define BB_HDR_LEN            5        // MHR B1: ver + flags + sender hash(2) + n  (no packet-level seqno)

// ---- metric constants ----
#define BB_METRIC_INF         0xFFFF   // infinity = unreachable / poison (route withdrawal)
#define BB_LINK_ETX_DEFAULT   100      // per-hop base cost (~ETX 1.0 scaled x100) when SNR unknown
#define BB_HYSTERESIS_PCT     15       // only switch primary next-hop on >= ~15% improvement (Design §3)

// One destination row in the DV table. dest is either a repeater hash or a region aggregate id.
// Babel-feasibility is kept PER DESTINATION (origin-independent — Design §3a point 3): `fd` is the best
// (lowest) cost we have ever ACHIEVED to this dest; a candidate is only feasible if strictly < fd.
struct BBDest {
  uint8_t  kind;                       // BB_DEST_REPEATER | BB_DEST_REGION
  uint8_t  id[BB_HASH_LEN];            // repeater hash prefix or region id (little-endian for region)
  uint16_t fd;                         // feasible distance for THIS dest (origin-independent Babel invariant)
  uint16_t metric;                     // our current advertised cost to dest via primary next-hop
  uint16_t seqno;                      // MHR B1: PER-DEST generation. For a learned route this is the
                                       //   origin's seqno we last accepted (propagated UNCHANGED on relay);
                                       //   for a self route this is the generation WE own and advance.
  uint8_t  next_hop[BB_HASH_LEN];      // primary feasible successor (next-hop neighbour hash)
  uint8_t  backup_hop[BB_HASH_LEN];    // pre-validated feasible-successor backup (Design §3, H3)
  uint16_t backup_metric;              // backup's cost (BB_METRIC_INF if no backup)
  uint32_t holddown_until;             // RTC secs: while >now, reject any WORSE alternative (Design §3a.2)
  uint32_t updated_secs;               // RTC secs of last update (LRU/least-stable eviction; 0 = empty)
  bool     has_next;                   // a live primary next-hop exists
  bool     has_backup;                 // a live backup next-hop exists
  bool     dirty;                      // trigger-on-change pending (couldn't fire yet due to rate-limit)
  bool     is_self;                    // self-originated route (cost 0, no next-hop, never poisoned)
};

// One DV neighbour: a direct repeater we have heard a DV packet from (the only valid next-hops).
struct BBNeighbour {
  uint8_t  hash[BB_HASH_LEN];          // neighbour hash prefix
  int8_t   snr;                        // EWMA-SNR (x4) if known, else -128
  uint32_t heard_secs;                 // RTC secs last heard (staleness gate; 0 = empty)
};

// Result of a route lookup, consumed by the data path's short-circuit decision.
struct BBRoute {
  bool    found;                       // a usable backbone route exists
  uint8_t next_hop[BB_HASH_LEN];       // next-hop neighbour to unicast toward
  uint16_t metric;                     // total cost (for "is it better?" comparison)
};

class Backbone {
public:
  Backbone();

  // Reset all tables to empty (call from ctor/begin()).
  void reset();

  // ---- metric helper: convert an EWMA-SNR (x4) into a per-link ETX-style cost (lower = better) ----
  static uint16_t linkCostFromSnr(int snr_x4);

  // Originate / refresh OUR OWN reachability as a destination: a cost-0 self repeater route (so
  //   neighbours learn "repeater self reachable via me"), and — if we are a border router — a cost-0
  //   aggregate for our home region. Call right before buildDVPayload(). `self_region` 0 = none.
  //   MHR B1: as the ORIGIN of these routes, this ADVANCES their per-dest seqno (the only place the
  //   network's seqno for a self-owned dest moves forward); relayers never touch a dest's seqno.
  void originateSelf(const uint8_t* self_hash, uint16_t self_region, bool is_border, uint32_t now_secs);

  // Record/refresh a direct DV neighbour (called when a DV packet is accepted from `hash`).
  void putNeighbour(const uint8_t* hash, int snr_x4, uint32_t now_secs);

  // Expire stale neighbours and run hold-down/feasible-successor maintenance. `holddown_s` is the
  // hold-down window applied to a route lost without a backup. Returns true if any route was lost (so
  // the caller may schedule a triggered poison announce).
  bool maintenance(uint32_t now_secs, uint16_t holddown_s);

  // ---- DV receive: parse and integrate an accepted zero-hop DV packet. Returns true if our own
  //      advertised state changed (so the caller may trigger-on-change). `self_hash` lets us ignore
  //      entries that point back at us. `holddown_s` stamps the hold-down on a poison-induced loss. ----
  bool onDVReceived(const mesh::Packet* pkt, const uint8_t* self_hash, int rx_snr_x4, uint32_t now_secs,
                    uint16_t holddown_s);

  // ---- DV transmit: build the next outgoing DV payload (entries we advertise to neighbours). Writes
  //      into `out` (>= MAX_PACKET_PAYLOAD), returns the byte length, or 0 if nothing to send. Includes
  //      poisoned (INF) routes for withdrawals. `is_border` marks us as an ABR. MHR B1: each entry now
  //      carries the route's PER-DEST seqno (row->seqno) — origin-advanced, relay-propagated unchanged. ----
  int buildDVPayload(uint8_t* out, const uint8_t* self_hash, bool is_border);

  // ---- route lookup for the data path's short-circuit: is `dest` reachable better than `flood_cost`?
  //      Only returns found=true when bb_enable (caller-gated), a live feasible route exists, and it is
  //      strictly better. Otherwise the caller falls back to today's flood-and-cache (never worse). ----
  BBRoute lookupRoute(uint8_t kind, const uint8_t* dest_id, uint16_t flood_cost) const;

  // Are there pending trigger-on-change announcements waiting for a free rate-limit slot?
  bool hasDirty() const;

  // Clear the dirty flags (call after a triggered announce has been built and sent).
  void clearDirty();

  // Debug/stats: number of live destinations / neighbours.
  int countDests() const;
  int countNeighbours() const;

private:
  BBDest      _dests[BB_MAX_DESTS];
  BBNeighbour _neigh[BB_MAX_NEIGHBOURS];
  uint16_t    _next_seqno;             // MHR B1: our own monotonically increasing ORIGIN seqno generator.
                                       //   Advanced once per originateSelf() and stamped onto our self
                                       //   routes (we are their seqno-owner). Learned routes keep the
                                       //   origin's seqno verbatim — they never read this counter.

  BBDest* findDest(uint8_t kind, const uint8_t* id);
  const BBDest* findDest(uint8_t kind, const uint8_t* id) const;
  BBDest* allocDest(uint8_t kind, const uint8_t* id, uint32_t now_secs);  // find-or-evict (least-stable)
  BBNeighbour* findNeighbour(const uint8_t* hash);
  const BBNeighbour* findNeighbour(const uint8_t* hash) const;
};
