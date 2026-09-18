/*
 * Minimal, self-contained STM32 HAL stub for the pcb-ai-agent compile-check.
 *
 * Purpose: give the *syntax check* of generated firmware templates the HAL
 * symbols they reference, without vendoring the real STMicroelectronics HAL
 * (which is vendor-licensed, huge and hardware-specific). Only declarations
 * are provided - nothing is defined or executed, and the compile-check runs
 * gcc/g++ with -fsyntax-only, so no object file is ever produced or linked.
 *
 * Everything here is written from the public HAL API surface used by the
 * project's 8 templates (see pcbai/firmware/templates.py) and the whitelist
 * in pcbai/firmware/validator.py. Register field values and base addresses
 * are irrelevant for a syntax check.
 */
#ifndef PCBAI_STUBS_STM32F4XX_HAL_H
#define PCBAI_STUBS_STM32F4XX_HAL_H

#include <stdint.h>

/* ----------------------------------------------------------------------- */
/* Status / pin state                                                      */
/* ----------------------------------------------------------------------- */

typedef enum {
    HAL_OK = 0x00U,
    HAL_ERROR = 0x01U,
    HAL_BUSY = 0x02U,
    HAL_TIMEOUT = 0x03U
} HAL_StatusTypeDef;

typedef enum { GPIO_PIN_RESET = 0, GPIO_PIN_SET = 1 } GPIO_PinState;

/* ----------------------------------------------------------------------- */
/* Peripheral register blocks (only the fields the templates touch)        */
/* ----------------------------------------------------------------------- */

typedef struct {
    uint32_t MODER;
    uint32_t OTYPER;
    uint32_t OSPEEDR;
    uint32_t PUPDR;
    uint32_t IDR;
    uint32_t ODR;
    uint32_t BSRR;
    uint32_t LCKR;
    uint32_t AFR[2];
} GPIO_TypeDef;

typedef struct {
    uint32_t CR1;
} TIM_TypeDef;

typedef struct {
    uint32_t CR1;
} ADC_TypeDef;

typedef struct {
    uint32_t CR1;
} USART_TypeDef;

typedef struct {
    uint32_t CR1;
} I2C_TypeDef;

/* ----------------------------------------------------------------------- */
/* Peripheral instances (syntax-only: addresses are placeholders)          */
/* ----------------------------------------------------------------------- */

#define GPIOA ((GPIO_TypeDef *) 0x40020000UL)
#define GPIOB ((GPIO_TypeDef *) 0x40020400UL)
#define GPIOC ((GPIO_TypeDef *) 0x40020800UL)
#define GPIOD ((GPIO_TypeDef *) 0x40020C00UL)
#define GPIOE ((GPIO_TypeDef *) 0x40021000UL)
#define GPIOF ((GPIO_TypeDef *) 0x40021400UL)
#define GPIOG ((GPIO_TypeDef *) 0x40021800UL)
#define GPIOH ((GPIO_TypeDef *) 0x40021C00UL)

#define TIM1 ((TIM_TypeDef *) 0x40010000UL)
#define TIM2 ((TIM_TypeDef *) 0x40000000UL)
#define TIM3 ((TIM_TypeDef *) 0x40000400UL)
#define TIM4 ((TIM_TypeDef *) 0x40000800UL)
#define TIM5 ((TIM_TypeDef *) 0x40000C00UL)
#define TIM6 ((TIM_TypeDef *) 0x40001000UL)
#define TIM7 ((TIM_TypeDef *) 0x40001400UL)
#define TIM8 ((TIM_TypeDef *) 0x40010400UL)
#define TIM12 ((TIM_TypeDef *) 0x40001800UL)
#define TIM13 ((TIM_TypeDef *) 0x40001C00UL)
#define TIM14 ((TIM_TypeDef *) 0x40002000UL)
#define TIM15 ((TIM_TypeDef *) 0x40014000UL)
#define TIM16 ((TIM_TypeDef *) 0x40014400UL)
#define TIM17 ((TIM_TypeDef *) 0x40014800UL)

#define ADC1 ((ADC_TypeDef *) 0x40012000UL)
#define ADC2 ((ADC_TypeDef *) 0x40012100UL)
#define ADC3 ((ADC_TypeDef *) 0x40012200UL)

#define USART1 ((USART_TypeDef *) 0x40011000UL)
#define USART2 ((USART_TypeDef *) 0x40004400UL)
#define USART3 ((USART_TypeDef *) 0x40004800UL)
#define UART4 ((USART_TypeDef *) 0x40004C00UL)
#define UART5 ((USART_TypeDef *) 0x40005000UL)
#define UART6 ((USART_TypeDef *) 0x40011400UL)
#define UART7 ((USART_TypeDef *) 0x40007800UL)
#define UART8 ((USART_TypeDef *) 0x40007C00UL)

#define I2C1 ((I2C_TypeDef *) 0x40005400UL)
#define I2C2 ((I2C_TypeDef *) 0x40005800UL)
#define I2C3 ((I2C_TypeDef *) 0x40005C00UL)

/* ----------------------------------------------------------------------- */
/* Init structures (named inner structs: valid C99, matches HAL usage)     */
/* ----------------------------------------------------------------------- */

typedef struct {
    uint32_t Pin;
    uint32_t Mode;
    uint32_t Pull;
    uint32_t Speed;
    uint32_t Alternate;
} GPIO_InitTypeDef;

typedef struct {
    uint32_t Prescaler;
    uint32_t CounterMode;
    uint32_t Period;
    uint32_t ClockDivision;
} TIM_Base_InitTypeDef;

typedef struct {
    TIM_TypeDef *Instance;
    TIM_Base_InitTypeDef Init;
} TIM_HandleTypeDef;

typedef struct {
    uint32_t OCMode;
    uint32_t Pulse;
    uint32_t OCPolarity;
} TIM_OC_InitTypeDef;

typedef struct {
    uint32_t ScanConvMode;
    uint32_t ContinuousConvMode;
    uint32_t DataAlign;
    uint32_t NbrOfConversion;
} ADC_InitTypeDef;

typedef struct {
    ADC_TypeDef *Instance;
    ADC_InitTypeDef Init;
} ADC_HandleTypeDef;

typedef struct {
    uint32_t Channel;
    uint32_t SamplingTime;
} ADC_ChannelConfTypeDef;

typedef struct {
    uint32_t BaudRate;
    uint32_t WordLength;
    uint32_t StopBits;
    uint32_t Parity;
    uint32_t Mode;
} UART_InitTypeDef;

typedef struct {
    USART_TypeDef *Instance;
    UART_InitTypeDef Init;
} UART_HandleTypeDef;

typedef struct {
    I2C_TypeDef *Instance;
} I2C_HandleTypeDef;

/* ----------------------------------------------------------------------- */
/* Constants                                                               */
/* ----------------------------------------------------------------------- */

#define GPIO_PIN_0 ((uint16_t) 0x0001)
#define GPIO_PIN_1 ((uint16_t) 0x0002)
#define GPIO_PIN_2 ((uint16_t) 0x0004)
#define GPIO_PIN_3 ((uint16_t) 0x0008)
#define GPIO_PIN_4 ((uint16_t) 0x0010)
#define GPIO_PIN_5 ((uint16_t) 0x0020)
#define GPIO_PIN_6 ((uint16_t) 0x0040)
#define GPIO_PIN_7 ((uint16_t) 0x0080)
#define GPIO_PIN_8 ((uint16_t) 0x0100)
#define GPIO_PIN_9 ((uint16_t) 0x0200)
#define GPIO_PIN_10 ((uint16_t) 0x0400)
#define GPIO_PIN_11 ((uint16_t) 0x0800)
#define GPIO_PIN_12 ((uint16_t) 0x1000)
#define GPIO_PIN_13 ((uint16_t) 0x2000)
#define GPIO_PIN_14 ((uint16_t) 0x4000)
#define GPIO_PIN_15 ((uint16_t) 0x8000)
#define GPIO_PIN_16 ((uint32_t) 0x00010000UL)
#define GPIO_PIN_17 ((uint32_t) 0x00020000UL)
#define GPIO_PIN_18 ((uint32_t) 0x00040000UL)
#define GPIO_PIN_19 ((uint32_t) 0x00080000UL)
#define GPIO_PIN_20 ((uint32_t) 0x00100000UL)
#define GPIO_PIN_21 ((uint32_t) 0x00200000UL)
#define GPIO_PIN_22 ((uint32_t) 0x00400000UL)
#define GPIO_PIN_23 ((uint32_t) 0x00800000UL)
#define GPIO_PIN_24 ((uint32_t) 0x01000000UL)
#define GPIO_PIN_25 ((uint32_t) 0x02000000UL)
#define GPIO_PIN_26 ((uint32_t) 0x04000000UL)
#define GPIO_PIN_27 ((uint32_t) 0x08000000UL)
#define GPIO_PIN_28 ((uint32_t) 0x10000000UL)
#define GPIO_PIN_29 ((uint32_t) 0x20000000UL)
#define GPIO_PIN_30 ((uint32_t) 0x40000000UL)
#define GPIO_PIN_31 ((uint32_t) 0x80000000UL)

#define GPIO_MODE_INPUT 0x00000000U
#define GPIO_MODE_OUTPUT_PP 0x00000001U
#define GPIO_MODE_OUTPUT_OD 0x00000011U
#define GPIO_MODE_AF_PP 0x00000002U
#define GPIO_MODE_AF_OD 0x00000012U
#define GPIO_MODE_ANALOG 0x00000003U

#define GPIO_NOPULL 0x00000000U
#define GPIO_PULLUP 0x00000001U
#define GPIO_PULLDOWN 0x00000002U

#define GPIO_SPEED_FREQ_LOW 0x00000000U
#define GPIO_SPEED_FREQ_MEDIUM 0x00000001U
#define GPIO_SPEED_FREQ_HIGH 0x00000002U
#define GPIO_SPEED_FREQ_VERY_HIGH 0x00000003U

#define TIM_COUNTERMODE_UP 0x00000000U
#define TIM_CLOCKDIVISION_DIV1 0x00000000U
#define TIM_OCMODE_PWM1 0x00000060U
#define TIM_OCPOLARITY_HIGH 0x00000000U

#define TIM_CHANNEL_1 0x00000000U
#define TIM_CHANNEL_2 0x00000004U
#define TIM_CHANNEL_3 0x00000008U
#define TIM_CHANNEL_4 0x0000000CU

#define DISABLE 0x00000000U
#define ENABLE 0x00000001U

#define ADC_SCAN_DISABLE 0x00000000U
#define ADC_DATAALIGN_RIGHT 0x00000000U
#define ADC_SAMPLETIME_1CYCLE_5 0x00000000U

#define ADC_CHANNEL_0 0x00000000U
#define ADC_CHANNEL_1 0x00000001U
#define ADC_CHANNEL_2 0x00000002U
#define ADC_CHANNEL_3 0x00000003U
#define ADC_CHANNEL_4 0x00000004U
#define ADC_CHANNEL_5 0x00000005U
#define ADC_CHANNEL_6 0x00000006U
#define ADC_CHANNEL_7 0x00000007U
#define ADC_CHANNEL_8 0x00000008U
#define ADC_CHANNEL_9 0x00000009U
#define ADC_CHANNEL_10 0x0000000AU
#define ADC_CHANNEL_11 0x0000000BU
#define ADC_CHANNEL_12 0x0000000CU
#define ADC_CHANNEL_13 0x0000000DU
#define ADC_CHANNEL_14 0x0000000EU
#define ADC_CHANNEL_15 0x0000000FU
#define ADC_CHANNEL_16 0x00000010U
#define ADC_CHANNEL_17 0x00000011U
#define ADC_CHANNEL_18 0x00000012U

#define UART_WORDLENGTH_8B 0x00000000U
#define UART_WORDLENGTH_9B 0x00001000U
#define UART_STOPBITS_1 0x00000000U
#define UART_STOPBITS_1_5 0x00003000U
#define UART_STOPBITS_2 0x00002000U
#define UART_PARITY_NONE 0x00000000U
#define UART_PARITY_EVEN 0x00000400U
#define UART_PARITY_ODD 0x00000600U
#define UART_MODE_TX_RX 0x0000000CU
#define UART_IT_RXNE 0x00000020U

/* ----------------------------------------------------------------------- */
/* HAL functions (declarations only; -fsyntax-only never links)            */
/* ----------------------------------------------------------------------- */

HAL_StatusTypeDef HAL_Init(void);
void HAL_Delay(uint32_t Delay);

void HAL_GPIO_Init(GPIO_TypeDef *GPIOx, GPIO_InitTypeDef *GPIO_Init);
void HAL_GPIO_DeInit(GPIO_TypeDef *GPIOx, uint32_t GPIO_Pin);
void HAL_GPIO_WritePin(GPIO_TypeDef *GPIOx, uint16_t GPIO_Pin, GPIO_PinState PinState);
GPIO_PinState HAL_GPIO_ReadPin(GPIO_TypeDef *GPIOx, uint16_t GPIO_Pin);
void HAL_GPIO_TogglePin(GPIO_TypeDef *GPIOx, uint16_t GPIO_Pin);

HAL_StatusTypeDef HAL_TIM_PWM_Init(TIM_HandleTypeDef *htim);
HAL_StatusTypeDef HAL_TIM_PWM_ConfigChannel(
    TIM_HandleTypeDef *htim, TIM_OC_InitTypeDef *sConfig, uint32_t Channel
);
HAL_StatusTypeDef HAL_TIM_PWM_Start(TIM_HandleTypeDef *htim, uint32_t Channel);
HAL_StatusTypeDef HAL_TIM_PWM_Stop(TIM_HandleTypeDef *htim, uint32_t Channel);
HAL_StatusTypeDef HAL_TIM_Base_Start(TIM_HandleTypeDef *htim);

HAL_StatusTypeDef HAL_ADC_Init(ADC_HandleTypeDef *hadc);
HAL_StatusTypeDef HAL_ADC_ConfigChannel(ADC_HandleTypeDef *hadc, ADC_ChannelConfTypeDef *sConfig);
HAL_StatusTypeDef HAL_ADC_Start(ADC_HandleTypeDef *hadc);
HAL_StatusTypeDef HAL_ADC_PollForConversion(ADC_HandleTypeDef *hadc, uint32_t Timeout);
uint32_t HAL_ADC_GetValue(ADC_HandleTypeDef *hadc);
HAL_StatusTypeDef HAL_ADC_Stop(ADC_HandleTypeDef *hadc);

HAL_StatusTypeDef HAL_UART_Init(UART_HandleTypeDef *huart);
HAL_StatusTypeDef HAL_UART_DeInit(UART_HandleTypeDef *huart);
HAL_StatusTypeDef HAL_UART_Transmit(
    UART_HandleTypeDef *huart, const uint8_t *pData, uint16_t Size, uint32_t Timeout
);
HAL_StatusTypeDef HAL_UART_Receive(
    UART_HandleTypeDef *huart, uint8_t *pData, uint16_t Size, uint32_t Timeout
);

HAL_StatusTypeDef HAL_I2C_Init(I2C_HandleTypeDef *hi2c);
HAL_StatusTypeDef HAL_I2C_Master_Transmit(
    I2C_HandleTypeDef *hi2c,
    uint16_t DevAddress,
    uint8_t *pData,
    uint16_t Size,
    uint32_t Timeout
);
HAL_StatusTypeDef HAL_I2C_Master_Receive(
    I2C_HandleTypeDef *hi2c,
    uint16_t DevAddress,
    uint8_t *pData,
    uint16_t Size,
    uint32_t Timeout
);

/* ----------------------------------------------------------------------- */
/* __HAL_RCC_* clock-enable macros (no-ops for a syntax check)             */
/* ----------------------------------------------------------------------- */

#define __HAL_RCC_GPIOA_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOB_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOC_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOD_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOE_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOF_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOG_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_GPIOH_CLK_ENABLE() do { } while (0)

#define __HAL_RCC_TIM1_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM2_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM3_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM4_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM5_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM6_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM7_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM8_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM12_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM13_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM14_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM15_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM16_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_TIM17_CLK_ENABLE() do { } while (0)

#define __HAL_RCC_ADC1_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_ADC2_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_ADC3_CLK_ENABLE() do { } while (0)

#define __HAL_RCC_USART1_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_USART2_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_USART3_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_UART4_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_UART5_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_UART6_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_UART7_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_UART8_CLK_ENABLE() do { } while (0)

#define __HAL_RCC_I2C1_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_I2C2_CLK_ENABLE() do { } while (0)
#define __HAL_RCC_I2C3_CLK_ENABLE() do { } while (0)

/* ----------------------------------------------------------------------- */
/* __HAL_* peripheral helpers (documented validator whitelist)             */
/* ----------------------------------------------------------------------- */

#define __HAL_TIM_SET_COMPARE(__HANDLE__, __CHANNEL__, __COMPARE__) do { } while (0)
#define __HAL_TIM_SET_PRESCALER(__HANDLE__, __PRESCALER__) do { } while (0)
#define __HAL_TIM_SET_AUTORELOAD(__HANDLE__, __AUTORELOAD__) do { } while (0)
#define __HAL_UART_ENABLE_IT(__HANDLE__, __IT__) do { } while (0)

#endif /* PCBAI_STUBS_STM32F4XX_HAL_H */
