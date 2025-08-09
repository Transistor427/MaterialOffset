from klipper.extras import gcode_macro

class MaterialOffset:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.records = []
        self.saved_offset = 0.0
        self.active = False
        
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
        self.gcode_move = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    def _handle_ready(self):
        self.gcode_move = self.printer.lookup_object('gcode_move')

    def _check_conflicts(self):
        for i in range(len(self.records)):
            for j in range(i + 1, len(self.records)):
                r1 = self.records[i]
                r2 = self.records[j]
                
                # Проверка пересечения по экструдеру
                extruder_intersect = not (r1['extruder'][1] < r2['extruder'][0] or 
                                          r2['extruder'][1] < r1['extruder'][0])
                
                # Проверка пересечения по столу
                bed_intersect = not (r1['bed'][1] < r2['bed'][0] or 
                                     r2['bed'][1] < r1['bed'][0])
                
                # Проверка пересечения по камере
                chamber_intersect = False
                if r1['chamber'][0] is not None and r2['chamber'][0] is not None:
                    chamber_intersect = not (r1['chamber'][1] < r2['chamber'][0] or 
                                             r2['chamber'][1] < r1['chamber'][0])
                
                # Проверка полного пересечения
                full_intersect = extruder_intersect and bed_intersect
                if r1['chamber'][0] is not None and r2['chamber'][0] is not None:
                    full_intersect = full_intersect and chamber_intersect
                
                if full_intersect:
                    raise self.printer.config_error(
                        f"Temperature ranges overlap between records {i+1} and {j+1}")

    def _get_heater_temp(self, name):
        heater = self.printer.lookup_object(name, None)
        if heater is not None and hasattr(heater, 'get_temp'):
            return heater.get_temp()[0]
        return None

    cmd_MATERIAL_OFFSET_ENABLE_help = "Enable material-based Z offset"
    def cmd_MATERIAL_OFFSET_ENABLE(self, gcmd):
        if self.gcode_move is None:
            raise gcmd.error("Printer not ready")
        
        # Сохранение текущего смещения
        self.saved_offset = self.gcode_move.gcode_offset[2]
        
        # Получение текущих температур
        extruder_temp = self._get_heater_temp('extruder')
        bed_temp = self._get_heater_temp('heater_bed')
        chamber_temp = self._get_heater_temp('chamber')
        
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
                if chamber_temp is None:
                    continue
                if not (record['chamber'][0] <= chamber_temp <= record['chamber'][1]):
                    continue
            
            matched_record = record
            break
        
        if matched_record is None:
            msg = "No material offset found for current temperatures:\n"
            msg += f"Extruder: {extruder_temp:.1f}°C, "
            msg += f"Bed: {bed_temp:.1f}°C"
            if chamber_temp is not None:
                msg += f", Chamber: {chamber_temp:.1f}°C"
            gcmd.respond_info(msg)
            return
        
        # Применение нового смещения
        offset_val = matched_record['offset']
        self.gcode.run_script(f"SET_GCODE_OFFSET Z={offset_val:.6f}")
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
        self.gcode.run_script(f"SET_GCODE_OFFSET Z={self.saved_offset:.6f}")
        self.active = False
        gcmd.respond_info(
            f"Material offset disabled. Restored Z offset: {self.saved_offset:.4f}mm"
        )

def load_config(config):
    return MaterialOffset(config)