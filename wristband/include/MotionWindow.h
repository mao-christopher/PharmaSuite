#pragma once
#include <cstddef>
#include <cstring>

// Timestamp-ordered, interleaved six-axis windows. Owned by acquisition task.
template<size_t Samples, size_t Axes> class MotionWindow {
 public:
  void reset() { head_=count_=0; }
  bool full() const { return count_==Samples; }
  void push(const float* frame) {
    std::memcpy(data_[head_],frame,Axes*sizeof(float));
    head_=(head_+1)%Samples;if(count_<Samples) ++count_;
  }
  bool copy(float* out) const {
    if(!full()) return false;
    for(size_t i=0;i<Samples;i++)
      std::memcpy(out+i*Axes,data_[(head_+i)%Samples],Axes*sizeof(float));
    return true;
  }
 private:
  float data_[Samples][Axes]{};
  size_t head_=0,count_=0;
};
