/*
 * Bridge so generated templates keep their default include name.
 *
 * pcbai.firmware templates default to ``hal_header="stm32f1xx_hal.h"``.
 * The bundled stub set is family-agnostic (one header covers the whole API
 * surface the templates use), so the F1 name simply forwards to it. This is
 * a compile-check artifact only - see stm32f4xx_hal.h for the rationale.
 */
#ifndef PCBAI_STUBS_STM32F1XX_HAL_H
#define PCBAI_STUBS_STM32F1XX_HAL_H

#include "stm32f4xx_hal.h"

#endif /* PCBAI_STUBS_STM32F1XX_HAL_H */