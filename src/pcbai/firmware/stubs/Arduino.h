/*
 * Minimal Arduino core stub for the pcb-ai-agent compile-check.
 *
 * Purpose: give the *syntax check* of generated Arduino sketches (compiled
 * as C++11, -fsyntax-only, with this header injected via -include like the
 * real Arduino toolchain does) the core symbols they reference. Only the
 * subset used by the project's 4 Arduino templates is provided; nothing is
 * linked or executed. The Serial class is deliberately C++ (Arduino is
 * C++), and the byte-typed println overloads exist so calls like
 * Serial.println(address, HEX) resolve unambiguously.
 */
#ifndef PCBAI_STUBS_ARDUINO_H
#define PCBAI_STUBS_ARDUINO_H

#include <stdint.h>
#include <stddef.h>

typedef uint8_t byte;
typedef bool boolean;

/* ----------------------------------------------------------------------- */
/* Pin modes and levels                                                    */
/* ----------------------------------------------------------------------- */

#define HIGH 0x1
#define LOW 0x0
#define INPUT 0x0
#define OUTPUT 0x1
#define INPUT_PULLUP 0x2
#define LED_BUILTIN 13

/* Numeric bases used by Serial.print(x, HEX); */
#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2

/* ----------------------------------------------------------------------- */
/* Pin aliases (Uno-style numbering used by the templates)                 */
/* ----------------------------------------------------------------------- */

#define D0 0
#define D1 1
#define D2 2
#define D3 3
#define D4 4
#define D5 5
#define D6 6
#define D7 7
#define D8 8
#define D9 9
#define D10 10
#define D11 11
#define D12 12
#define D13 13

#define A0 14
#define A1 15
#define A2 16
#define A3 17
#define A4 18
#define A5 19
#define A6 20
#define A7 21
#define A8 22
#define A9 23
#define A10 24
#define A11 25
#define A12 26
#define A13 27
#define A14 28
#define A15 29

/* ----------------------------------------------------------------------- */
/* Core functions (declarations only; -fsyntax-only never links)          */
/* ----------------------------------------------------------------------- */

void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t value);
int digitalRead(uint8_t pin);
void analogWrite(uint8_t pin, int value);
int analogRead(uint8_t pin);
void delay(unsigned long ms);
void delayMicroseconds(unsigned int us);
unsigned long millis(void);
unsigned long micros(void);

void setup(void);
void loop(void);

/* ----------------------------------------------------------------------- */
/* Serial                                                                */
/* ----------------------------------------------------------------------- */

class Serial_ {
public:
    void begin(unsigned long baud);
    void begin(unsigned long baud, uint8_t config);

    void print(const char *text);
    void print(char value);
    void print(int value, int base = DEC);
    void print(unsigned int value, int base = DEC);
    void print(long value, int base = DEC);
    void print(unsigned long value, int base = DEC);
    void print(double value, int digits = 2);

    void println();
    void println(const char *text);
    void println(char value);
    void println(int value, int base = DEC);
    void println(unsigned int value, int base = DEC);
    void println(long value, int base = DEC);
    void println(unsigned long value, int base = DEC);
    void println(unsigned char value, int base);
    void println(double value, int digits = 2);
};

extern Serial_ Serial;

#endif /* PCBAI_STUBS_ARDUINO_H */