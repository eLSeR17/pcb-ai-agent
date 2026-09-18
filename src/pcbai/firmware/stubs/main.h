/*
 * Minimal main.h for the pcb-ai-agent compile-check.
 *
 * In a real CubeMX project main.h pulls the active HAL header and declares
 * the project entry points. Here it only forwards to the bundled stub HAL;
 * generated templates include "main.h" first and then their hal_header.
 */
#ifndef PCBAI_STUBS_MAIN_H
#define PCBAI_STUBS_MAIN_H

#include "stm32f4xx_hal.h"

void Error_Handler(void);

#endif /* PCBAI_STUBS_MAIN_H */