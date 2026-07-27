from math import isnan

# Heater keys used in material preset sections
HEATER_KEYS = ('extruder', 'bed', 'chamber')

# Default Klipper object names for each heater key
DEFAULT_HEATER_OBJECTS = {
    'extruder': 'extruder',
    'bed': 'heater_bed',
    'chamber': None,  # resolved via chamber_sensor or auto-detect
}

# Typical chamber object names in Klipper
CHAMBER_SENSOR_CANDIDATES = (
    'heater_generic chamber',
    'temperature_sensor chamber',
    'chamber',
)


def _ranges_overlap(a, b):
    """Return True if closed intervals a and b overlap."""
    return a[0] <= b[1] and b[0] <= a[1]


def _parse_range(section, value):
    parts = value.split(':', 1)
    if len(parts) != 2:
        raise section.error("Неверный формат диапазона: %s" % (value,))
    try:
        v1 = float(parts[0].strip())
        v2 = float(parts[1].strip())
    except ValueError:
        raise section.error("Неверные числа в диапазоне: %s" % (value,))
    return (min(v1, v2), max(v1, v2))


class MaterialOffset:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.records = []
        self.applied_offset = 0.0
        self.active_preset = None
        self.active = False
        self.gcode_move = None
        self.toolhead = None

        # Optional object-name overrides in [material_offset]
        self.heater_objects = {
            'extruder': config.get('extruder_sensor', DEFAULT_HEATER_OBJECTS['extruder']),
            'bed': config.get('bed_sensor', DEFAULT_HEATER_OBJECTS['bed']),
            'chamber': config.get('chamber_sensor', None),
        }

        prefix = 'material_offset '
        for section in config.get_prefix_sections(prefix):
            try:
                self.records.append(self._parse_record(section))
            except Exception as e:
                raise config.error(
                    "Ошибка в секции '%s': %s" % (section.get_name(), str(e))
                )

        if not self.records:
            raise config.error(
                "Не заданы секции пресетов [material_offset <name>]"
            )

        self._check_conflicts()

        self.gcode.register_command(
            'MATERIAL_OFFSET_ENABLE',
            self.cmd_MATERIAL_OFFSET_ENABLE,
            desc=self.cmd_MATERIAL_OFFSET_ENABLE_help,
        )
        self.gcode.register_command(
            'MATERIAL_OFFSET_DISABLE',
            self.cmd_MATERIAL_OFFSET_DISABLE,
            desc=self.cmd_MATERIAL_OFFSET_DISABLE_help,
        )
        self.printer.register_event_handler('klippy:ready', self._handle_ready)

    def _parse_record(self, section):
        offset = section.getfloat('offset')
        heaters = {}
        for key in HEATER_KEYS:
            raw = section.get(key, None)
            if raw is not None:
                heaters[key] = _parse_range(section, raw)
        if not heaters:
            raise section.error(
                "Нужно указать хотя бы один из параметров: extruder, bed, chamber"
            )
        return {
            'name': section.get_name().split(None, 1)[-1],
            'offset': offset,
            'heaters': heaters,
        }

    def _handle_ready(self):
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.toolhead = self.printer.lookup_object('toolhead')

    def _check_conflicts(self):
        """Reject presets that cannot be disambiguated by specificity.

        Overlap is allowed when one preset's heater set is a proper subset of
        another's (e.g. ABS without chamber vs ABS HT with chamber). Matching
        then prefers the more specific preset.
        """
        for i in range(len(self.records)):
            for j in range(i + 1, len(self.records)):
                r1 = self.records[i]
                r2 = self.records[j]
                if self._presets_conflict(r1, r2):
                    raise self.printer.config_error(
                        "Конфликт диапазонов температур между пресетами '%s' и '%s'"
                        % (r1['name'], r2['name'])
                    )

    def _presets_conflict(self, r1, r2):
        keys1 = set(r1['heaters'])
        keys2 = set(r2['heaters'])
        shared = keys1 & keys2

        if shared:
            for key in shared:
                if not _ranges_overlap(r1['heaters'][key], r2['heaters'][key]):
                    return False
        elif keys1 and keys2:
            # No common heaters: both can match the same printer state
            return True

        if keys1 == keys2:
            return True
        # Proper subset: more specific preset wins at match time
        if keys1 < keys2 or keys2 < keys1:
            return False
        # Incomparable heater sets with overlapping shared ranges
        return True

    def _get_temp(self, name):
        if not name:
            return None
        try:
            obj = self.printer.lookup_object(name)
            if hasattr(obj, 'get_status'):
                status = obj.get_status(self.reactor.monotonic())
                temp = status.get('temperature')
                if temp is not None and not isnan(temp):
                    return float(temp)
        except Exception:
            pass
        return None

    def _get_chamber_temp(self):
        if self.heater_objects['chamber']:
            return self._get_temp(self.heater_objects['chamber'])
        for name in CHAMBER_SENSOR_CANDIDATES:
            temp = self._get_temp(name)
            if temp is not None:
                return temp
        return None

    def _read_temperatures(self):
        temps = {
            'extruder': self._get_temp(self.heater_objects['extruder']),
            'bed': self._get_temp(self.heater_objects['bed']),
            'chamber': self._get_chamber_temp(),
        }
        return temps

    def _record_matches(self, record, temps):
        for key, (tmin, tmax) in record['heaters'].items():
            temp = temps.get(key)
            if temp is None or isnan(temp):
                return False
            if not (tmin <= temp <= tmax):
                return False
        return True

    def _find_best_match(self, temps):
        matches = [r for r in self.records if self._record_matches(r, temps)]
        if not matches:
            return None, None
        matches.sort(key=lambda r: len(r['heaters']), reverse=True)
        best = matches[0]
        tied = [r for r in matches if len(r['heaters']) == len(best['heaters'])]
        if len(tied) > 1:
            names = ', '.join(r['name'] for r in tied)
            return None, names
        return best, None

    def _format_temps(self, temps):
        labels = {
            'extruder': 'Экструдер',
            'bed': 'Стол',
            'chamber': 'Камера',
        }
        parts = []
        for key in HEATER_KEYS:
            temp = temps.get(key)
            label = labels[key]
            if temp is None:
                parts.append('%s: н/д' % (label,))
            else:
                parts.append('%s: %.1f°C' % (label, temp))
        return ', '.join(parts)

    cmd_MATERIAL_OFFSET_ENABLE_help = 'Контроль высоты первого слоя активирован'

    def cmd_MATERIAL_OFFSET_ENABLE(self, gcmd):
        if self.toolhead is None or self.gcode_move is None:
            raise gcmd.error('Принтер не готов')
        if self.active:
            gcmd.respond_info(
                "Смещение материала уже активно: '%s' Z_ADJUST=%.4fмм"
                % (self.active_preset, self.applied_offset)
            )
            return

        temps = self._read_temperatures()
        matched, ambiguous = self._find_best_match(temps)
        if ambiguous:
            raise gcmd.error(
                'Неоднозначное совпадение пресетов смещения: %s\n  %s'
                % (ambiguous, self._format_temps(temps))
            )
        if matched is None:
            gcmd.respond_info(
                'Не найдено смещение материала для текущих температур:\n  %s'
                % (self._format_temps(temps),)
            )
            return

        self.applied_offset = matched['offset']
        self.active_preset = matched['name']
        self.gcode.run_script_from_command(
            'SET_GCODE_OFFSET Z_ADJUST=%.6f' % (self.applied_offset,)
        )
        self.active = True

        used = ', '.join(
            '%s %.0f:%.0f' % (k, matched['heaters'][k][0], matched['heaters'][k][1])
            for k in HEATER_KEYS
            if k in matched['heaters']
        )
        gcmd.respond_info(
            "Смещение материала '%s' применено: Z_ADJUST=%.4fмм (%s)\n  %s"
            % (matched['name'], self.applied_offset, used, self._format_temps(temps))
        )

    cmd_MATERIAL_OFFSET_DISABLE_help = 'Отключить контроль высоты первого слоя'

    def cmd_MATERIAL_OFFSET_DISABLE(self, gcmd):
        if not self.active:
            gcmd.respond_info('Контроль высоты первого слоя отключен')
            return
        self.gcode.run_script_from_command(
            'SET_GCODE_OFFSET Z_ADJUST=%.6f' % (-self.applied_offset,)
        )
        removed = self.applied_offset
        preset = self.active_preset
        self.applied_offset = 0.0
        self.active_preset = None
        self.active = False
        gcmd.respond_info(
            "Смещение материала '%s' отключено. Возвращен Z_ADJUST: %.4fмм"
            % (preset, removed)
        )


def load_config(config):
    return MaterialOffset(config)
