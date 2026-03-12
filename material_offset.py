from math import isnan

# Типичные имена объектов камеры в Klipper (heater_generic / temperature_sensor)
CHAMBER_SENSOR_CANDIDATES = [
    'heater_generic chamber',
    'temperature_sensor chamber',
    'chamber',
]

class MaterialOffset:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.records = []
        self.saved_offset = 0.0
        self.active = False
        self.gcode_move = None
        self.toolhead = None
        # Опциональное имя датчика камеры (если не задано — пробуем стандартные)
        self.chamber_sensor_name = config.get('chamber_sensor', None)
        # Парсинг конфигурации
        prefix = 'material_offset '
        sections = config.get_prefix_sections(prefix)
        for section in sections:
            try:
                # Извлечение параметров записи
                offset = section.getfloat('offset')
                extruder_range = section.get('extruder')
                bed_range = section.get('heater_bed')
                chamber_range = section.get('chamber', None)
                # Парсинг диапазонов температур
                def parse_range(s):
                    parts = s.split(':', 1)
                    if len(parts) != 2:
                        raise section.error(f"Invalid range format: {s}")
                    try:
                        v1 = float(parts[0].strip())
                        v2 = float(parts[1].strip())
                        return min(v1, v2), max(v1, v2)
                    except ValueError:
                        raise section.error(f"Invalid numbers in range: {s}")
                extruder_min, extruder_max = parse_range(extruder_range)
                bed_min, bed_max = parse_range(bed_range)
                chamber_min = chamber_max = None
                if chamber_range:
                    chamber_min, chamber_max = parse_range(chamber_range)
                # Сохранение записи
                self.records.append({
                    'offset': offset,
                    'extruder': (extruder_min, extruder_max),
                    'bed': (bed_min, bed_max),
                    'chamber': (chamber_min, chamber_max)
                })
            except Exception as e:
                raise config.error(f"Error in section '{section.get_name()}': {str(e)}")
        # Проверка конфликтов диапазонов
        self._check_conflicts()
        # Регистрация команд G-кода
        self.gcode.register_command(
            'MATERIAL_OFFSET_ENABLE',
            self.cmd_MATERIAL_OFFSET_ENABLE,
            desc=self.cmd_MATERIAL_OFFSET_ENABLE_help
        )
        self.gcode.register_command(
            'MATERIAL_OFFSET_DISABLE',
            self.cmd_MATERIAL_OFFSET_DISABLE,
            desc=self.cmd_MATERIAL_OFFSET_DISABLE_help
        )
        # Инициализация объектов принтера
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self):
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.toolhead = self.printer.lookup_object('toolhead')

    def _check_conflicts(self):
        for i in range(len(self.records)):
            for j in range(i + 1, len(self.records)):
                r1 = self.records[i]
                r2 = self.records[j]
                # Проверка пересечения по экструдеру
                extruder_intersect = (r1['extruder'][0] <= r2['extruder'][1] and
                                      r2['extruder'][0] <= r1['extruder'][1])
                # Проверка пересечения по столу
                bed_intersect = (r1['bed'][0] <= r2['bed'][1] and
                                 r2['bed'][0] <= r1['bed'][1])
                # Проверка пересечения по камере
                chamber_intersect = False
                if r1['chamber'][0] is not None and r2['chamber'][0] is not None:
                    chamber_intersect = (r1['chamber'][0] <= r2['chamber'][1] and
                                         r2['chamber'][0] <= r1['chamber'][1])
                # Проверка полного пересечения
                full_intersect = extruder_intersect and bed_intersect
                if r1['chamber'][0] is not None and r2['chamber'][0] is not None:
                    full_intersect = full_intersect and chamber_intersect
                if full_intersect:
                    raise self.printer.config_error(
                        f"Temperature ranges overlap between records {i+1} and {j+1}")

    def _get_temp(self, name):
        """Получает температуру по имени объекта (heater, temperature_sensor и т.д.)."""
        try:
            obj = self.printer.lookup_object(name)
            if hasattr(obj, 'get_status'):
                eventtime = self.reactor.monotonic()
                status = obj.get_status(eventtime)
                temp = status.get('temperature')
                if temp is not None and not isnan(temp):
                    return float(temp)
        except Exception:
            pass
        return None

    def _get_chamber_temp(self):
        """Получает температуру камеры. В Klipper камера обычно: heater_generic chamber или temperature_sensor chamber."""
        if self.chamber_sensor_name:
            return self._get_temp(self.chamber_sensor_name)
        for name in CHAMBER_SENSOR_CANDIDATES:
            temp = self._get_temp(name)
            if temp is not None:
                return temp
        return None

    cmd_MATERIAL_OFFSET_ENABLE_help = "Enable material-based Z offset"
    def cmd_MATERIAL_OFFSET_ENABLE(self, gcmd):
        if self.toolhead is None:
            raise gcmd.error("Printer not ready")
        # Получение текущих температур
        extruder_temp = self._get_temp('extruder')
        bed_temp = self._get_temp('heater_bed')
        chamber_temp = self._get_chamber_temp()
        if extruder_temp is None:
            raise gcmd.error("Extruder temperature not available")
        if bed_temp is None:
            raise gcmd.error("Bed temperature not available")
        # Поиск подходящей записи
        matched_record = None
        for record in self.records:
            # Проверка диапазона экструдера
            if not (record['extruder'][0] <= extruder_temp <= record['extruder'][1]):
                continue
            # Проверка диапазона стола
            if not (record['bed'][0] <= bed_temp <= record['bed'][1]):
                continue
            # Проверка диапазона камеры (если указан)
            if record['chamber'][0] is not None:
                if chamber_temp is None or isnan(chamber_temp):
                    continue
                if not (record['chamber'][0] <= chamber_temp <= record['chamber'][1]):
                    continue
            matched_record = record
            break
        if matched_record is None:
            needs_chamber = any(r['chamber'][0] is not None for r in self.records)
            msg = "No material offset found for current temperatures:\n"
            msg += f"  Extruder: {extruder_temp:.1f}°C, Bed: {bed_temp:.1f}°C"
            if chamber_temp is not None:
                msg += f", Chamber: {chamber_temp:.1f}°C"
            else:
                msg += f", Chamber: not available"
                if needs_chamber:
                    msg += " (required for matching record; add chamber_sensor to [material_offset] if name differs)"
            gcmd.respond_info(msg)
            return
        # Применение нового смещения
        offset_val = matched_record['offset']
        # Вычисляем абсолютное смещение для установки
        new_offset = self.saved_offset + offset_val
        self.gcode.run_script_from_command(f"SET_GCODE_OFFSET Z={new_offset:.6f}")
        self.active = True
        gcmd.respond_info(
            f"Material offset applied: Z={offset_val:.4f}mm\n"
            f"Temperatures - Extruder: {extruder_temp:.1f}°C, "
            f"Bed: {bed_temp:.1f}°C"
            + (f", Chamber: {chamber_temp:.1f}°C" if chamber_temp is not None else "")
        )

    cmd_MATERIAL_OFFSET_DISABLE_help = "Disable material-based Z offset"
    def cmd_MATERIAL_OFFSET_DISABLE(self, gcmd):
        if not self.active:
            gcmd.respond_info("Material offset is not active")
            return
        # Восстановление оригинального смещения
        self.gcode.run_script_from_command(f"SET_GCODE_OFFSET Z={self.saved_offset:.6f}")
        self.active = False
        gcmd.respond_info(
            f"Material offset disabled. Restored Z offset: {self.saved_offset:.4f}mm"
        )

def load_config(config):
    return MaterialOffset(config)