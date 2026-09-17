# Checklist de Diseño de PCB — Requisitos por Categoría de Componente

> Documento de referencia para el rediseño del sistema de auditoría (v2).
> Organizado por categoría de componente con las condiciones REALES que un
> ingeniero de hardware senior verifica (fuentes: ST AN2586, AN2867, AN4080,
> AN5165; Microchip AN2519; IEC 61760 / 60384; guide de layout de fabricantes;
> experiencia industrial).
>
> Al final: tabla de "qué es verificable desde un netlist/DSL" (lo que el
> sistema puede auditar) vs "qué requiere datos extra" (lo que necesitaría
> ampliación).

---

## 0. REGLAS GLOBALES DE ALIMENTACIÓN (aplican a toda placa)

| # | Regla | Detalle técnico |
|---|-------|-----------------|
| P1 | Decoupling por par de pines VDD/GND | 100 nF cerámico por par + bulk (4.7-10 µF), colocado a **< 5 mm** del pin, por debajo si es posible. Nunca un único valor para todo. |
| P2 | Decoupling local + global | Por pin (lo más cerca posible) + global en el rail (varios valores en paralelo: 100 nF + 1 µF + 10 µF) para amplio espectro de frecuencias. |
| P3 | Bucle de alta corriente pequeño | La corrente de conmutación: condensador → pin → GND/VDD, lazo mínimo. Vías en paralelo para reducir inductancia. |
| P4 | Plano GND dedicado | Stack-up multicapa con plano GND completo y sin cortes bajo rutas críticas. |
| P5 | Cercanía regulador→MCU | El regulador alimenta al MCU con traza corta y filtrada (ferrita opcional), nunca con traza larga sin desacoplar. |
| P6 | Pines de potencia sin conectar | Verificar que todos los VDD/VSS/VDDA/VDDA18/VBAT estén conectados (no flotantes). |
| P7 | Separación de dominios analógicos | VDDA independiente + filtro RC/LC si hay ADC; VDDA ≥ VDD en todo momento; diodo schottky opcional entre VDD y VDDA. |
| P8 | Capacidad de entrada de potencia | El layout: trazas de potencia anchas (>25 mil típico para 3.3V), planos de potencia, sin estrangulamientos. |
| P9 | Supervisor de arranque | POR/PDR correcto (mín. 1 µF en NRST, o supervisor externo si regulador lento). |
| P10 | Protección ESD/TVS en entrada de potencia | TVS en el conector de alimentación; diodo de polaridad inversa (serie o PMOS high-side). |

---

## 1. FUENTES DE ALIMENTACIÓN (reguladores)

### 1.1 LDO
| # | Regla | Detalle |
|---|-------|---------|
| LDO1 | Condensadores según datasheet | Valores de entrada/salida EXACTOS del LDO (no "100 nF genérico"); respetar ESR mínimo si es requisito. |
| LDO2 | C de salida con ESR correcto | Tantalio/electrolítico si el LDO lo exige; ESR incorrecto → oscilación. |
| LDO3 | Disipación térmica | P = (Vin-Vout)·I; verificar que la potencia disipada está dentro del derating (SOT-23 ≈ 0.3-0.5 W, DPAK ≈ 1.5-2 W). |
| LDO4 | Dropout | Vin mín = Vout + Vdropout + margen (5-10%). |

### 1.2 Buck/Boost
| # | Regla | Detalle |
|---|-------|---------|
| BUCK1 | Inductor dimensionado | L según fórmula del controlador; corriente de saturación ≥ 1.2-1.5 × Iout máx. |
| BUCK2 | Diodo/circuito de freewheel | Schottky con VRRM ≥ 1.5 × Vin, I ≥ Iout. |
| BUCK3 | C de salida | Según ripple requerido; ESR bajo; ceramic + bulk. |
| BUCK4 | Compensación | Red RC según controlador; NO copiar valores de otra placa sin recalcular. |
| BUCK5 | Layout del lazo de conmutación | El lazo de alta frecuencia (switch→L→diode→C) compacto, sin vías, área mínima. |

---

## 2. CLOCKS / OSCILADORES

### 2.1 Cristal (HSE / LSE)
| # | Regla | Detalle |
|---|-------|---------|
| X1 | Carga correcta | CL = (CL1·CL2)/(CL1+CL2) + Cstray (stray ≈ 3-5 pF típico). NO usar el valor del datasheet del cristal a secas. |
| X2 | C1=C2 simétricos | Valores iguales (típ. 5-25 pF cerámico NP0/C0G); si CL del cristal = 12 pF y stray 4 pF → CL1=CL2 = 2·(12-4) = 16 pF, no 22 pF genérico. |
| X3 | LSE 32k | CL total ≤ 7 pF para LSE (según ST); no usar cristal de 12.5 pF. |
| X4 | ESR y margen de arranque | Margen de ganancia Sf ≥ 5 (HSE), ≥ 3 (LSE). ESR bajo = arranque fiable. |
| X5 | Proximidad | Cristal + caps LO MÁS CERCANOS posible al MCU; sin vías en la traza del cristal; trazas cortas y simétricas. |
| X6 | Aislamiento | Guard ring de GND alrededor; sin señales de alta frecuencia cerca; sin test points en bucle del oscilador. |
| X7 | Planos | GND plano local bajo el oscilador, plano GND completo para retorno. |
| X8 | Bypass HSE | Si se usa HSE bypass: conectar OSC_IN a reloj ext; OSC_OUT en alta impedancia. |

### 2.2 Frecuencias y deriva
| # | Regla | Detalle |
|---|-------|---------|
| X9 | Ajuste por precisión | LSE: precisión RTC → deriva ppm; calcular CL correcto para ≤ 5 ppm si es requisito. |
| X10 | Startup | Verificar arranque en frío/caliente; tiempo de arranque 1-5 s típico LSE. |

---

## 3. MICROCONTROLADOR (STM32/AVR/ESP32 genérico)

| # | Regla | Detalle |
|---|-------|---------|
| MCU1 | Todos los pines de potencia conectados | VDD, VDDA, VSS, VSSA, VBAT, VCAP, VREF+ — NINGUNO flotante. |
| MCU2 | Decoupling por par | 100 nF + bulk por cada par VDD/VSS; C extra (10 µF) para VDDA. |
| MCU3 | Reset | NRST: pull-up 10-100 kΩ + cap 100 nF-1 µF (o supervisor externo); mínimo pulso de reset según datasheet; NO dejar flotante. |
| MCU4 | Boot pins | Pull-up/down según modo de boot requerido (boot0/boot1) con resistencia definida; no flotantes. |
| MCU5 | SWD/JTAG | SWDIO: pull-up (o pull según estado de reset); SWCLK: pull-down; no flotantes. |
| MCU6 | Oscilador | Ver sección 2 (cristal + caps + layout). |
| MCU7 | I/O no usadas | Fijar a nivel definido (externo o pull interno), no dejar flotantes (EMC + consumo). |
| MCU8 | VCAP/bypass del regulador interno | Si MCU tiene VCAP (STM32 F0/F3/L4...): conectar el cap correcto (típ. 2×100 nF) tal cual datasheet, CERCANO al pin. |
| MCU9 | PDR_ON / supervisor | Configurar PDR_ON según diseño; si se usa bypass de regulador, VDDA/VCAP según AN. |
| MCU10 | Pines NC del embalaje | Si sobran pines del paquete: dejar correctamente (no conectar a nada flotante que hiciera daño). |

---

## 4. INTERFACES / COMUNICACIONES

### 4.1 UART
| # | Regla | Detalle |
|---|-------|---------|
| U1 | Cruce TX/RX | Conector UART: cruzar TX↔RX entre ambos lados. |
| U2 | Estados ociosos | Pull-up en líneas si bus puede quedar flotante; nivel idle = '1' (inverso de UART). |
| U3 | Niveles | Nivel correcto (3.3V vs 5V), traducción si procede. |

### 4.2 I²C
| # | Regla | Detalle |
|---|-------|---------|
| I1 | Pull-ups | Resistencias en SCL y SDA (típ. 4.7 kΩ @3.3V, 2.2 kΩ @5V; ajustar según capacidad del bus y velocidad). |
| I2 | Cálculo de pull-up | Rpull = (Vcc - VIL) / IOL (para trise según Cbus). Para 400 kHz: trise ≤ 300 ns. |
| I3 | Direcciones | Sin conflicto de direcciones entre dispositivos. |
| I4 | Nivel lógico | Pull-up al Vcc de la lógica del bus, no a Vcc del MCU a secas si hay decoupling distinto. |

### 4.3 SPI
| # | Regla | Detalle |
|---|-------|---------|
| S1 | Terminación | Si trazas largas o alta velocidad: serie + resistencia de terminación según impedancia. |
| S2 | CS bien definido | Chip Select con pull-up/pull-down para evitar falsos esclavos. |

### 4.4 USB
| # | Regla | Detalle |
|---|-------|---------|
| USB1 | Impedancia diferencial | Pistas D+/D- 90 Ω diferencial; longitudes igualadas cuanto sea posible. |
| USB2 | ESD | TVS/ESD en D+/D-/VBUS en el conector (baja capacidad para alta velocidad). |
| USB3 | Filtro | Inductor/filtro de modo común en VBUS si ruido. |
| USB4 | Terminación | Resistencia serie (22 Ω típ.) cerca del driver si no hay transceiver integrado. |

### 4.5 CAN
| # | Regla | Detalle |
|---|-------|---------|
| CAN1 | Terminación 120 Ω | En AMBOS extremos del bus; split termination (2×60 Ω + cap) para mejorar EMC. |
| CAN2 | Modo común | Choke de modo común para reducción EMI. |
| CAN3 | ESD | TVS en CAN_H/CAN_L. |

---

## 5. PASIVOS — condiciones por clase

### 5.1 Resistencias
| # | Regla | Detalle |
|---|-------|---------|
| R1 | Valor | Siempre presente y correcto (R-class necesita valor). |
| R2 | Potencia | P = I²·R (o V²/R); elegir con derating 50% (resistor de 0.1 W para cálculos ≤ 0.05 W). |
| R3 | Tolerancia | Según aplicación (1% para divisores precisos, 5% ok para bias). |
| R4 | Footprint | Existente y adecuado a la potencia (0603≈0.1W, 0805≈0.125W, 1206≈0.25W). |
| R5 | Voltaje | Vmax según tamaño (0603 ≈ 75V, 0805 ≈ 150V...); respetar. |
| R6 | Serie preferida | Usar valores E12/E24 (no "valor suelto"); 330 Ω vs 333 Ω. **→ implementado v2: `E_SERIES_COMPLIANCE` (warning).** |

### 5.2 Condensadores
| # | Regla | Detalle |
|---|-------|---------|
| C1 | Valor + tensión | Valor presente; tensión nominal ≥ 1.5-2 × tensión de trabajo (derating: MLCC pierden ~60-80% cap con DC bias). **→ implementado v2 (parcial): `CAP_DERATING` (info) cuando el valor lleva tensión y el rail tiene nombre numérico.** |
| C2 | Tipo dieléctrico | X5R/X7R para general; C0G/NP0 para precisión (oscilador, filtros); Y5V evitar. |
| C3 | Polaridad | Electrolíticos/tantalio: polaridad correcta y marcada; nunca invertir. |
| C4 | ESR | Verificar ESR para ripple (C de salida de fuente). |
| C5 | Footprint + temperatura | Footprint existente; rango de temperatura adecuado. |

### 5.3 Inductores
| # | Regla | Detalle |
|---|-------|---------|
| L1 | Saturación | Isat ≥ 1.2 × Iout máx (los inductores pierden inductancia con corriente). |
| L2 | DCR | Bajo para no perder eficiencia (P = I²·DCR). |
| L3 | Frecuencia de resonancia propia | SRF >> frecuencia de trabajo. |

### 5.4 LEDs
| # | Regla | Detalle |
|---|-------|---------|
| LED1 | Limitador de corriente SIEMPRE | R serie: R = (Vcc - Vf) / If, con Vf del color (rojo 1.8-2.2V, azul/blanco 3.0-3.4V). **→ implementado v2 (parcial): `LED_NO_LIMITER` (v1, sin R) + `LED_SERIES_RESISTOR` (v2, R = 0 Ω o < 22 Ω); el cálculo real de R por If sigue fuera de alcance.** |
| LED2 | If correcta | Respetar If máx del LED (típ. 20 mA); no exceder. |
| LED3 | Polaridad | Cátodo/ánodo correctos (netlist: LED connecta + y -). |
| LED4 | Potencia de la R | P = If²·R con derating 50%. |

### 5.5 Diodos (rectificadores, schottky, zener, TVS)
| # | Regla | Detalle |
|---|-------|---------|
| D1 | Polaridad | Marcada y correcta (cinta = cátodo). |
| D2 | If y VRRM | Corriente promedio y tensión inversa con margen (VRRM ≥ 1.5-2 × tensión inversa real). |
| D3 | Zener: potencia | Pz = Vz·Iz con derating. |
| D4 | TVS | Vclamp compat con el circuito; colocar EN el conector/entrada (primer elemento). |

### 5.6 Transistores (BJT/MOSFET)
| # | Regla | Detalle |
|---|-------|---------|
| Q1 | BJT: R de base | Rbase = (Vin - Vbe)/Ib con Ib = Ic/β (β real, no ideal; usar β/10 para saturación). |
| Q2 | BJT: disipación | P = Vce_sat·Ic (saturación) o Vce·Iccarga (lineal). |
| Q3 | MOSFET: Vgs | Vgs threshold vs tensión de drive real (3.3V → no usar MOSFET con Vgs(th) 4V). |
| Q4 | MOSFET: Rds | P = I²·Rds(on) · duty; disipación total. |
| Q5 | Gate: pull-down / pull-up | Gate nunca flotante (pull-down para NMOS, pull-up para PMOS cuando el driver no está). |
| Q6 | Gate: resistencia serie opcional | Rg para reducir ringing EMI (10-100 Ω). |
| Q7 | Diodo flyback | En cargas inductivas (relé, bobina, motor): diodo en paralelo a la carga. |

### 5.7 Relés / polos
| # | Regla | Detalle |
|---|-------|---------|
| REL1 | Flyback | Diodo anti-retorno en bobina. |
| REL2 | Conducción | Transistor con margen; Vce/Vds del driver acorde a la corriente de la bobina. |

---

## 6. CONECTORES

| # | Regla | Detalle |
|---|-------|---------|
| CON1 | Footprint correcto | Del fabricante/datasheet; número de pines real coincidente. |
| CON2 | Pin 1 / polaridad | Marcado visible (pin 1, punta de flecha, muesca). |
| CON3 | Orientación | Conector orientado para ensamblaje (cable no choca con otros comps). |
| CON4 | Retención mecánica | Uso de agujeros de montaje/tornillo si es conector de esfuerzo. |
| CON5 | ESD en E/S | TVS en todas las señales que salen de la placa. |
| CON6 | Clearance/creepage | Distancias según tensión de trabajo (mains: creepage adecuado entre primario/secundario). |

---

## 7. LAYOUT / FÍSICO / MECÁNICO

| # | Regla | Detalle |
|---|-------|---------|
| L1 | Stackup | 2 capas (sencillo) o 4+ (GND/VDD adyacentes, impedancia controlada). |
| L2 | Plano GND completo | Sin cortes bajo rutas críticas; aperturas mínimas. |
| L3 | Capacidades de potencia | Trazas de potencia dimensionadas; plano de potencia aislado de señales analógicas. |
| L4 | Separación de bloques | Alto consumo / digital / sensitivo / RF separados físicamente. |
| L5 | Área de oscilador | Ver sección 2 (guard ring, lejos de EMI). |
| L6 | Vias de tierra con stitching | Densidad adecuada; stitching de GND en bordes si es multicapa. |
| L7 | Agujeros de montaje | 4 mín, en esquinas; conectados a GND con cuidado (aislar si es necesario). |
| L8 | Componentes sensibles | Bajo vibr/estrés mecánico: no usar SMD grandes sin esqueleto. |
| L9 | Ancho de pista | Según corriente: tabla IPC-2221 (1A ≈ 0.2-0.3 mm en 1 oz externo, con margen). |
| L10 | Serigrafía | Nombres de red en conectores, designators legibles, marca de versión/revisión. |
| L11 | Flechas de polaridad | LEDs, electrolíticos, diodos marcados en serigrafía. |

---

## 8. EMC / EMI / ESD

| # | Regla | Detalle |
|---|-------|---------|
| E1 | Filtrado en entradas | Ferrita + cap en entradas de alimentación y I/O. |
| E2 | Retorno ESD | TVS con retorno corto y ancho a GND/chassis; confinado. |
| E3 | Separación de planos | Analógico/digital con conexión en UN solo punto. |
| E4 | Oscilador aislado | Guard ring, lejos de fuentes de ruido (ver sección 2). |
| E5 | Alta velocidad | Impedancia controlada, sin stubs, planos completos, series terminación. |
| E6 | Bypass correcto | Ver sección 0 (P1-P3). |

---

## 9. FABRICACIÓN / DFM

| # | Regla | Detalle |
|---|-------|---------|
| F1 | Footprints verificados | Contra datasheet del componente (pad ≠ pin footprint). |
| F2 | Anillo anular | ≥ 0.15-0.2 mm (IPC); vía/pad cohesionado. |
| F3 | Soldermask | Aperturas correctas; no tapar pads. |
| F4 | Serigrafía completa | Referencias, fiscalidad, versión, logo, fecha. |
| F5 | Test points | Accesibles para ICT/flying probe si procede. |
| F6 | BOM completo | Ref, valor, footprint, fabricante, nº pieza, cantidad. |
| F7 | DRC/ERC limpio | Sin errores (no solo sin warnings). |
| F8 | Netlist vs schematic | Coherente (regla de DER: reimportable, sin comps huérfanos). |

---

## 10. RESUMEN — QUÉ ES VERIFICABLE DESDE EL SISTEMA ACTUAL (netlist/DSL) vs QUÉ REQUIERE MÁS

Esta tabla es la frontera entre lo que el auditor de hoy puede y lo que el V2 debería cubrir.

| Categoría | Verificable HOY (netlist) | Requiere datos extra (V2) |
|-----------|--------------------------|--------------------------|
| **Valores de R/C/L en circuito** | R sin limitador (LED) ✓ · R sin valor ✓ · footprint ✓ | Valor CORRECTO vs cálculo de la red (dimensionado) |
| **Lógica de driver** | Pines sin conectar ✓ · nets flotantes ✓ · sin driver activo ✓ | Transistor saturado? · R de base? · Vgs correcto? · flyback? |
| **Alimentación** | Comps/pines de potencia flotantes ✓ (parcial) | Decoupling por pin? · bypass correcto? · loops? (requiere layout/plano) |
| **Clock** | — | Carga CL correcta? · proximidad? (requiere symb/valor y layout) |
| **Interfaces** | Nets de bus presentes ✓ | Pull-ups I²C? · terminación CAN/USB? · cruce UART? |
| **Mecánico/DFM** | Footprints ✓ · comps sin valor ✓ | Orientación? · serigrafía? · test points? (requiere .kicad_sch/.kicad_pcb) |
| **EMC** | — | Stackup? · planos? · guard ring? (requiere layout .kicad_pcb) |

**Conclusión honesta**: el netlist solo
da conectividad (quién está conectado a quién). El sistema hoy verifica **la
mitad estructural** (conectividad, valores presentes, footprints). Para la
otra mitad (dimensionado correcto, drivers, EMC, layout físico) hace falta
**más entrada** (schematic con valores de parámetros, o footprint/BOM para
cálculos de potencia) — es la frontera natural que el V2 debe cruzar.

### 10.1 Estado de implementación del sprint v2

Tres reglas netlist-verificables de esta checklist pasan de "requiere datos
extra" a implementadas (código en `src/pcbai/design/audit.py`, fixtures
deterministas en `tests/fixtures/`, casos de test en `tests/test_audit.py`):

| Regla checklist | Regla del auditor | Severidad | Qué comprueba desde el netlist |
|-----------------|-------------------|-----------|--------------------------------|
| R6 — serie preferida | `E_SERIES_COMPLIANCE` | warning | la mantisa del valor R/C pertenece a E12/E24 (tolerancia 0.5 %): `333` avisa, `330` no |
| LED1 — limitador | `LED_SERIES_RESISTOR` | warning | R serie del LED = 0 Ω (cortocircuito) o < 22 Ω (no limita a 3.3-5 V); ≥ 22 Ω pasa |
| C1 — tensión | `CAP_DERATING` | info | electrolítico con tensión en el valor (`10uF 50V`) y rail numérico (`5V`/`3V3`) con Vnom < 1.5 × Vrail |

Sigue fuera de alcance de este sprint (documentado, no implementado):
pull-ups I²C por valor (I1/I2), cálculo real de R serie del LED por If, y
todo lo que depende de layout/plano/footprint (secciones 7 y 8).
