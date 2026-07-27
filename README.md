# MaterialOffset

Модуль Klipper: Z-offset первого слоя по текущим температурам.

## Как работает

В конфиге задаются пресеты с диапазонами `extruder` / `bed` / `chamber` и смещением `offset`.  
При `MATERIAL_OFFSET_ENABLE` сравниваются текущие температуры с пресетами; подходит самый специфичный (с большим числом указанных нагревателей). Неуказанные нагреватели игнорируются.

## Команды

- `MATERIAL_OFFSET_ENABLE` — применить offset
- `MATERIAL_OFFSET_DISABLE` — вернуть прежний Z-offset

## Установка

Скопировать `material_offset.py` в каталог модулей Klipper и подключить конфиг:

```ini
[include material_offset.cfg]
```

Пример пресетов — в `material_offset.cfg`.
