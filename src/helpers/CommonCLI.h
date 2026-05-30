#pragma once

#include "Mesh.h"
#include <helpers/IdentityStore.h>
#include <helpers/SensorManager.h>
#include <helpers/ClientACL.h>
#include <helpers/RegionMap.h>

#if defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE)
#define WITH_BRIDGE
#endif

#define ADVERT_LOC_NONE       0
#define ADVERT_LOC_SHARE      1
#define ADVERT_LOC_PREFS      2

#define LOOP_DETECT_OFF       0
#define LOOP_DETECT_MINIMAL   1
#define LOOP_DETECT_MODERATE  2
#define LOOP_DETECT_STRICT    3

struct NodePrefs { // persisted to file
  float airtime_factor;
  char node_name[32];
  double node_lat, node_lon;
  char password[16];
  float freq;
  int8_t tx_power_dbm;
  uint8_t disable_fwd;
  uint8_t advert_interval;       // minutes / 2
  uint8_t flood_advert_interval; // hours
  float rx_delay_base;
  float tx_delay_factor;
  char guest_password[16];
  float direct_tx_delay_factor;
  uint32_t guard;
  uint8_t sf;
  uint8_t cr;
  uint8_t allow_read_only;
  uint8_t multi_acks;
  float bw;
  uint8_t flood_max;
  uint8_t interference_threshold;
  uint8_t agc_reset_interval; // secs / 4
  // Bridge settings
  uint8_t bridge_enabled; // boolean
  uint16_t bridge_delay;  // milliseconds (default 500 ms)
  uint8_t bridge_pkt_src; // 0 = logTx, 1 = logRx (default logTx)
  uint32_t bridge_baud;   // 9600, 19200, 38400, 57600, 115200 (default 115200)
  uint8_t bridge_channel; // 1-14 (ESP-NOW only)
  char bridge_secret[16]; // for XOR encryption of bridge packets (ESP-NOW only)
  // Power setting
  uint8_t powersaving_enabled; // boolean
  // Gps settings
  uint8_t gps_enabled;
  uint32_t gps_interval; // in seconds
  uint8_t advert_loc_policy;
  uint32_t discovery_mod_timestamp;
  float adc_multiplier;
  char owner_info[120];
  uint8_t rx_boosted_gain; // power settings
  uint8_t path_hash_mode;   // which path mode to use when sending
  uint8_t loop_detect;
  // MHR: SNR weighting for flood-rebroadcast timing. 0.0 = upstream behaviour (pure random backoff);
  //      >0 biases strong-SNR receptions to rebroadcast earlier so quality links lead the flood.
  //      Appended at end of struct for forward-compatible persistence. Reversible: set txsnrweight 0.
  float tx_snr_weight;
  // MHR: hop-count weighting for flood-rebroadcast timing — the PRIMARY quality lever (the real-data
  //       study found hop count far more reliable than SNR for path length). 0.0 = off. Appended at end
  //       of struct for forward-compatible persistence. Reversible: set txhopweight 0.
  float tx_hop_weight;
  // MHR: Stufe B — redundancy-guarded flood suppression (the 5-guard mechanism, see
  //       docs/MHR/study/Suppression_Design.md / SUPPRESSION_VALIDATION.md). All fields appended at the
  //       STRUCT END for forward/backward-compatible persistence; old config files keep these defaults.
  //       supp_enable=0 (DEFAULT) => behaviour EXACTLY as Stufe A, no extra effect anywhere. The whole
  //       feature is dormant until deliberately enabled per CLI (set supp.enable 1) after bench validation.
  uint8_t supp_enable;     // 0 = off (default, = Stufe A); 1 = suppression active
  uint8_t supp_min_degree; // G1: never stay silent below this many known neighbours (validated default 3..4)
  uint8_t supp_k_cover;    // G2: required number of distinct qualified cover senders heard in the backoff
  int8_t  supp_snr_floor;  // G4: minimum EWMA-SNR (dB) of a cover sender to count it
  uint8_t supp_prob;       // G5: suppress probability in percent (0..100); a fraction always still sends
  // MHR: Best-of-N path discovery AT THE DESTINATION. When we are the target of a flood path-discovery,
  //      upstream sends the reciprocal path-return immediately using the FIRST copy heard ("first packet
  //      wins" — often a detour). Best-of-N instead opens a short collection window: the payload is still
  //      processed EXACTLY ONCE on the first copy (dedup unchanged), but the path-return is deferred and
  //      the BEST path (fewest hops, then strongest SNR) seen among all duplicate copies within the window
  //      is returned. Purely LOCAL, no packet-format change → mixed-firmware safe; stock peers just receive
  //      the same path-return type, maybe a window later and via a shorter path. Fields appended at the
  //      STRUCT END for forward/backward-compatible persistence; old config files keep these defaults.
  //      bofn_enable=1 (DEFAULT ON) is safe because the window only changes WHICH path is reported, never
  //      whether/how often the payload is delivered. Reversible per CLI: set bofn.enable 0.
  uint8_t  bofn_enable;     // 0 = off (= Stufe A first-wins); 1 = Best-of-N at destination (default)
  uint16_t bofn_window_ms;  // collection window in ms (0 from old file => restore default)
};

class CommonCLICallbacks {
public:
  virtual void savePrefs() = 0;
  virtual const char* getFirmwareVer() = 0;
  virtual const char* getBuildDate() = 0;
  virtual const char* getRole() = 0;
  virtual bool formatFileSystem() = 0;
  virtual void sendSelfAdvertisement(int delay_millis, bool flood) = 0;
  virtual void updateAdvertTimer() = 0;
  virtual void updateFloodAdvertTimer() = 0;
  virtual void setLoggingOn(bool enable) = 0;
  virtual void eraseLogFile() = 0;
  virtual void dumpLogFile() = 0;
  virtual void setTxPower(int8_t power_dbm) = 0;
  virtual void formatNeighborsReply(char *reply) = 0;
  virtual void removeNeighbor(const uint8_t* pubkey, int key_len) {
    // no op by default
  };
  virtual void formatStatsReply(char *reply) = 0;
  virtual void formatRadioStatsReply(char *reply) = 0;
  virtual void formatPacketStatsReply(char *reply) = 0;
  virtual mesh::LocalIdentity& getSelfId() = 0;
  virtual void saveIdentity(const mesh::LocalIdentity& new_id) = 0;
  virtual void clearStats() = 0;
  virtual void applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) = 0;

  virtual void startRegionsLoad() {
    // no op by default
  }
  virtual bool saveRegions() {
    return false;
  }
  virtual void onDefaultRegionChanged(const RegionEntry* r) {
    // no op by default
  }

  virtual void setBridgeState(bool enable) {
    // no op by default
  };

  virtual void restartBridge() {
    // no op by default
  };

  virtual void setRxBoostedGain(bool enable) {
    // no op by default
  };
};

class CommonCLI {
  mesh::RTCClock* _rtc;
  NodePrefs* _prefs;
  CommonCLICallbacks* _callbacks;
  mesh::MainBoard* _board;
  SensorManager* _sensors;
  RegionMap* _region_map;
  ClientACL* _acl;
  char tmp[PRV_KEY_SIZE*2 + 4];

  mesh::RTCClock* getRTCClock() { return _rtc; }
  void savePrefs();
  void loadPrefsInt(FILESYSTEM* _fs, const char* filename);

  void handleRegionCmd(char* command, char* reply);
  void handleGetCmd(uint32_t sender_timestamp, char* command, char* reply);
  void handleSetCmd(uint32_t sender_timestamp, char* command, char* reply);

public:
  CommonCLI(mesh::MainBoard& board, mesh::RTCClock& rtc, SensorManager& sensors, RegionMap& region_map, ClientACL& acl, NodePrefs* prefs, CommonCLICallbacks* callbacks)
      : _board(&board), _rtc(&rtc), _sensors(&sensors), _region_map(&region_map), _acl(&acl), _prefs(prefs), _callbacks(callbacks) { }

  void loadPrefs(FILESYSTEM* _fs);
  void savePrefs(FILESYSTEM* _fs);
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply);
  uint8_t buildAdvertData(uint8_t node_type, uint8_t* app_data);
};
