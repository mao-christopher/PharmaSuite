#pragma once
#include <cstdint>
#include <cstring>
#include <cmath>

// Application policy, not calibrated probabilities. Raw scores always print.
class EventGate {
 public:
  static constexpr float threshold=0.65f, margin=0.15f;
  const char* state() const { return state_==1?"held_estimated":state_==2?"down_estimated":"unknown"; }
  const char* reason() const { return reason_; }
  uint32_t firstEvidenceMs() const { return firstEvidence_; }
  void reset() {
    state_=streak_=lastClass_=background_=activeClass_=0;
    haveEvent_=false;havePrediction_=false;firstEvidence_=0;reason_="reset";
  }
  const char* update(const char* label,float score,float runnerUp,uint32_t now) {
    // A skipped/stale prediction must not complete an old confirmation streak.
    if(havePrediction_ && now-lastPrediction_>750) { streak_=0;lastClass_=0;background_=0; }
    havePrediction_=true;lastPrediction_=now;
    if(!label || !std::isfinite(score) || !std::isfinite(runnerUp)) {
      streak_=lastClass_=background_=0;reason_="invalid";return nullptr;
    }
    const int cls=!std::strcmp(label,"pick_up")?1:!std::strcmp(label,"put_down")?2:0;
    const bool confident=score>=threshold && score-runnerUp>=margin;
    const bool backgroundLabel=!std::strcmp(label,"idle") || !std::strcmp(label,"random");
    if(!cls && confident && backgroundLabel) {
      if(background_<2) ++background_;
      if(background_>=2) activeClass_=0;
    } else background_=0;
    if(!cls || !confident) {
      streak_=lastClass_=0;reason_=confident && backgroundLabel?"background":"uncertain";return nullptr;
    }
    if(cls!=lastClass_) { streak_=0;firstEvidence_=now; }
    lastClass_=cls;if(streak_<2) ++streak_;
    // One confident prediction is sufficient to activate an event.
    if(activeClass_==cls) { reason_="same_episode";return nullptr; }
    if(haveEvent_ && now-lastEvent_<1500) { reason_="cooldown";return nullptr; }
    // An opposite class may transition directly, without an intervening idle.
    // Estimated held/down state is descriptive; it must not veto new episodes.
    state_=activeClass_=cls;lastEvent_=now;haveEvent_=true;reason_="event";
    return cls==1?"PICKUP_LIKELY":"PUTDOWN_LIKELY";
  }
 private:
  int state_=0,streak_=0,lastClass_=0,background_=0,activeClass_=0;
  uint32_t lastEvent_=0,lastPrediction_=0,firstEvidence_=0;
  bool haveEvent_=false,havePrediction_=false;
  const char* reason_="reset";
};
