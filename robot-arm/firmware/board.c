#include "board.h"
#include "motion.h"

#include <stddef.h>

/* Small register subset; addresses and offsets match ST's STM32F446 device header. */
#define REG32(address) (*(volatile uint32_t *)(address))
#define RCC_CR       REG32(0x40023800U)
#define RCC_CFGR     REG32(0x40023808U)
#define RCC_AHB1ENR  REG32(0x40023830U)
#define RCC_APB1ENR  REG32(0x40023840U)
#define RCC_APB1RSTR REG32(0x40023820U)
#define SYST_CSR     REG32(0xE000E010U)
#define SYST_RVR     REG32(0xE000E014U)
#define SYST_CVR     REG32(0xE000E018U)
#define SCB_SHPR3    REG32(0xE000ED20U)
#define NVIC_ISER    ((volatile uint32_t *)0xE000E100U)
#define NVIC_IPR     ((volatile uint8_t *)0xE000E400U)
#define CPU_HZ       16000000U
#define SERIAL_BAUD  115200U
#define RX_CAPACITY  512U
#define STEP1_PIN    3U  /* PB3:  Arduino D3, connected to PULSE- */
#define DIR1_PIN     5U  /* PB5:  Arduino D4 */
#define STEP2_PIN    7U  /* PC7:  Arduino D9, connected to PULSE- */
#define DIR2_PIN     9U  /* PA9:  Arduino D8, connected to DIR- */

typedef struct {
    volatile uint32_t moder, otyper, ospeedr, pupdr, idr, odr, bsrr, lckr, afr[2];
} Gpio;
typedef struct {
    volatile uint32_t sr, dr, brr, cr1, cr2, cr3, gtpr;
} Usart;
typedef struct {
    volatile uint32_t cr1, cr2, smcr, dier, sr, egr, ccmr1, ccmr2, ccer, cnt, psc, arr;
} Timer;
#define GPIOA  ((Gpio *)0x40020000U)
#define GPIOB  ((Gpio *)0x40020400U)
#define GPIOC  ((Gpio *)0x40020800U)
#define USART2 ((Usart *)0x40004400U)
#define TIM2   ((Timer *)0x40000000U)

static volatile uint32_t millis;
static volatile unsigned rx_head, rx_tail;
static volatile unsigned char rx_data[RX_CAPACITY];
static volatile bool serial_fault, stop_request;

uint32_t board_irq_save(void)
{
    uint32_t previous;
    __asm volatile("mrs %0, primask\ncpsid i" : "=r"(previous) :: "memory");
    return previous;
}

void board_irq_restore(uint32_t saved)
{
    __asm volatile("msr primask, %0" :: "r"(saved) : "memory");
}

static void output_pin(Gpio *gpio, unsigned pin)
{
    gpio->moder = (gpio->moder & ~(3U << (2U * pin))) | (1U << (2U * pin));
    gpio->otyper &= ~(1U << pin);
    gpio->ospeedr = (gpio->ospeedr & ~(3U << (2U * pin))) | (1U << (2U * pin));
    gpio->pupdr &= ~(3U << (2U * pin));
}

void board_init(void)
{
    /* Use HSI so this firmware works without any ST-LINK clock/jumper dependency. */
    RCC_CR |= 1U;
    while ((RCC_CR & (1U << 1)) == 0) { }
    RCC_CFGR &= ~3U;
    while ((RCC_CFGR & (3U << 2)) != 0) { }
    RCC_CFGR &= ~((15U << 4) | (7U << 10) | (7U << 13));
    RCC_AHB1ENR |= 7U;
    (void)RCC_AHB1ENR;
    GPIOB->bsrr = ((1U << STEP1_PIN) | (1U << DIR1_PIN)) << 16;
    GPIOC->bsrr = (1U << STEP2_PIN) << 16;
    GPIOA->bsrr = (1U << DIR2_PIN) << 16;
    output_pin(GPIOB, STEP1_PIN); output_pin(GPIOB, DIR1_PIN);
    output_pin(GPIOC, STEP2_PIN); output_pin(GPIOA, DIR2_PIN);
    output_pin(GPIOA, 5);
    GPIOC->moder &= ~(3U << 26);
    GPIOC->pupdr = (GPIOC->pupdr & ~(3U << 26)) | (1U << 26);
    for (unsigned pin = 2; pin <= 3; ++pin) {
        GPIOA->moder = (GPIOA->moder & ~(3U << (2U * pin))) | (2U << (2U * pin));
        GPIOA->afr[0] = (GPIOA->afr[0] & ~(15U << (4U * pin))) | (7U << (4U * pin));
        GPIOA->pupdr = (GPIOA->pupdr & ~(3U << (2U * pin))) | (1U << (2U * pin));
    }
    RCC_APB1ENR |= (1U << 17) | 1U;
    (void)RCC_APB1ENR;
    RCC_APB1RSTR |= (1U << 17) | 1U;
    RCC_APB1RSTR &= ~((1U << 17) | 1U);
    USART2->brr = (CPU_HZ + SERIAL_BAUD / 2U) / SERIAL_BAUD;
    USART2->cr3 = 1U; /* Error interrupt enable. */
    USART2->cr1 = (1U << 13) | (1U << 5) | (1U << 3) | (1U << 2);
    NVIC_IPR[38] = 0x40U;
    NVIC_ISER[1] = 1U << (38U - 32U);

    SYST_RVR = CPU_HZ / 1000U - 1U;
    SYST_CVR = 0;
    SCB_SHPR3 = (SCB_SHPR3 & ~(255U << 24)) | (0xE0U << 24);
    SYST_CSR = 7U;
    TIM2->psc = 0;
    TIM2->arr = CPU_HZ / MOTION_TICK_HZ - 1U;
    TIM2->egr = 1U;
    TIM2->sr = 0;
    TIM2->dier = 1U;
    NVIC_IPR[28] = 0x20U;
    NVIC_ISER[0] = 1U << 28;
    TIM2->cr1 = 1U;
}

void SysTick_Handler(void) { ++millis; }
uint32_t board_millis(void) { return millis; }

/* main.c owns TIM2_IRQHandler; acknowledge its update event here. */
void board_timer_ack(void) { TIM2->sr = 0; }

void board_step_levels(uint8_t mask)
{
    GPIOB->bsrr = 1U << ((mask & 1U) != 0 ? STEP1_PIN : STEP1_PIN + 16U);
    GPIOC->bsrr = 1U << ((mask & 2U) != 0 ? STEP2_PIN : STEP2_PIN + 16U);
}

void board_directions(const int direction[2], const bool inverted[2])
{
    const bool first = (direction[0] > 0) != inverted[0];
    const bool second = (direction[1] > 0) != inverted[1];
    GPIOB->bsrr = 1U << (first ? DIR1_PIN : DIR1_PIN + 16U);
    GPIOA->bsrr = 1U << (second ? DIR2_PIN : DIR2_PIN + 16U);
}

void board_led(bool on) { GPIOA->bsrr = 1U << (on ? 5U : 21U); }
bool board_stop_pressed(void) { return (GPIOC->idr & (1U << 13)) == 0; }

void USART2_IRQHandler(void)
{
    const uint32_t status = USART2->sr;
    if ((status & ((1U << 5) | 15U)) == 0) { return; }
    const unsigned char ch = (unsigned char)USART2->dr;
    if ((status & 15U) != 0) {
        serial_fault = true;
        stop_request = true;
        return;
    }
    if (ch == '!') {
        stop_request = true;
        return;
    }
    const unsigned next = (rx_head + 1U) & (RX_CAPACITY - 1U);
    if (next == rx_tail) {
        serial_fault = true;
        stop_request = true;
        return;
    }
    rx_data[rx_head] = ch;
    __asm volatile("dmb" ::: "memory");
    rx_head = next;
}

void board_serial_write(const char *text)
{
    for (; *text != '\0'; ++text) {
        while ((USART2->sr & (1U << 7)) == 0) { }
        USART2->dr = (uint32_t)(unsigned char)*text;
    }
}

int board_serial_read(void)
{
    if (rx_tail == rx_head) { return -1; }
    const int ch = rx_data[rx_tail];
    rx_tail = (rx_tail + 1U) & (RX_CAPACITY - 1U);
    return ch;
}

bool board_serial_fault(void)
{
    const uint32_t saved = board_irq_save();
    const bool result = serial_fault;
    serial_fault = false;
    board_irq_restore(saved);
    return result;
}

bool board_take_stop_request(void)
{
    const bool result = stop_request;
    stop_request = false;
    return result;
}

void board_serial_discard(void)
{
    const uint32_t saved = board_irq_save();
    rx_tail = rx_head;
    board_irq_restore(saved);
}
