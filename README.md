# MaterialOffset

Модуль Klipper: Z-offset первого слоя по текущим температурам.

## Как работает

В конфиге задаются пресеты с диапазонами `extruder` / `bed` / `chamber` и смещением `offset`.  
При `MATERIAL_OFFSET_ENABLE` сравниваются текущие температуры с пресетами; подходит самый специфичный (с большим числом указанных нагревателей). Неуказанные нагреватели игнорируются.

## Команды

- `MATERIAL_OFFSET_ENABLE` — применить offset
- `MATERIAL_OFFSET_DISABLE` — вернуть прежний Z-offset

## Установка

```bash
cd ~ && git clone https://github.com/Transistor427/MaterialOffset.git && sudo ln -s ~/MaterialOffset/material_offset.py ~/klipper/klippy/extras/material_offset.py
```

Подключить конфиг (или скопировать пресеты из `material_offset.cfg`):

```ini
[include material_offset.cfg]
```
