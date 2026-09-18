/*
 * Minimal Arduino Wire (I2C) stub for the pcb-ai-agent compile-check.
 *
 * Covers the TwoWire surface used by the arduino_i2c_scan template
 * (begin/beginTransmission/endTransmission and the classic constructor
 * singleton Wire). Declarations only - the check never links or executes.
 */
#ifndef PCBAI_STUBS_WIRE_H
#define PCBAI_STUBS_WIRE_H

#include "Arduino.h"

class TwoWire {
public:
    void begin();
    void begin(uint8_t address);
    void begin(int sda, int scl);
    void setClock(uint32_t frequency);
    void beginTransmission(uint8_t address);
    uint8_t endTransmission();
    uint8_t endTransmission(uint8_t sendStop);
    uint8_t requestFrom(uint8_t address, uint8_t quantity);
    size_t write(uint8_t value);
    size_t write(const uint8_t *data, size_t quantity);
    int available();
    int read();
};

extern TwoWire Wire;

#endif /* PCBAI_STUBS_WIRE_H */