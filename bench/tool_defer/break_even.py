"""A fracao critica: acima de quantos % de turnos precisando de ferramenta deferida
o deferimento passa a custar mais do que economiza."""
ESQ_DECL, ESQ_DEF, SISTEMA = 3224, 1347, 348

def custo(passos, cresc, esquema, h0=200):
    total, h = 0, h0
    for _ in range(passos):
        total += SISTEMA + esquema + h
        h += cresc
    return total

print(f"{'passos':>7} {'cresc':>7} {'extras':>7} {'p critico':>11}   leitura")
for passos in (3, 5, 8):
    for cresc in (300, 800):
        for extras in (1, 2):
            a = custo(passos, cresc, ESQ_DECL)                 # declarado, sempre
            b_nucleo = custo(passos, cresc, ESQ_DEF)           # deferido, nao precisou
            b_deferida = custo(passos + extras, cresc, ESQ_DEF)  # deferido, precisou
            ganho = a - b_nucleo          # > 0
            perda = b_deferida - a        # pode ser < 0 (ainda economiza)
            if perda <= 0:
                print(f"{passos:>7} {cresc:>7} {extras:>7} {'sempre bom':>11}   economiza mesmo quando precisa")
                continue
            p = ganho / (ganho + perda)
            print(f"{passos:>7} {cresc:>7} {extras:>7} {p*100:>10.0f}%   acima disso, deferir custa mais")
