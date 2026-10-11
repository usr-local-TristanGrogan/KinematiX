#include <stdint.h>

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;
extern int main(void);

void Reset_Handler(void)
{
    /* Enable the Cortex-M4 single-precision FPU before any floating-point code. */
    *(volatile uint32_t *)0xE000ED88U |= 0xFU << 20;
    __asm volatile("dsb\nisb" ::: "memory");
    *(volatile uint32_t *)0xE000ED08U = 0x08000000U;
    uint32_t *source = &_sidata;
    for (uint32_t *destination = &_sdata; destination < &_edata; ++destination) {
        *destination = *source++;
    }
    for (uint32_t *destination = &_sbss; destination < &_ebss; ++destination) {
        *destination = 0;
    }
    (void)main();
    for (;;) {
        __asm volatile("wfi");
    }
}

void Default_Handler(void)
{
    __asm volatile("cpsid i" ::: "memory");
    /* Force both STEP outputs low on an unexpected interrupt/fault. */
    *(volatile uint32_t *)0x40020418U = (1U << 3) << 16; /* D3: PB3 */
    *(volatile uint32_t *)0x40020818U = (1U << 7) << 16; /* D9: PC7 */
    for (;;) {
        __asm volatile("wfi");
    }
}
