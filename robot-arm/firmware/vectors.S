.syntax unified
.cpu cortex-m4
.thumb

.section .isr_vector,"a",%progbits
.balign 512
.global vector_table
vector_table:
    .word _estack
    .word Reset_Handler
    .word Default_Handler /* NMI */
    .word Default_Handler /* HardFault */
    .word Default_Handler /* MemManage */
    .word Default_Handler /* BusFault */
    .word Default_Handler /* UsageFault */
    .word 0, 0, 0, 0
    .word Default_Handler /* SVCall */
    .word Default_Handler /* DebugMon */
    .word 0
    .word Default_Handler /* PendSV */
    .word SysTick_Handler
    .rept 28
    .word Default_Handler
    .endr
    .word TIM2_IRQHandler /* External IRQ 28 */
    .rept 9
    .word Default_Handler
    .endr
    .word USART2_IRQHandler /* External IRQ 38 */
    .rept 58
    .word Default_Handler
    .endr
.size vector_table, .-vector_table

.section .note.GNU-stack,"",%progbits
