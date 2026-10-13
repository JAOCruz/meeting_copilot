"""Captura de audio desde BlackHole (u otro dispositivo) y conversión a 16 kHz mono float32."""
import queue
import time

import numpy as np
import sounddevice as sd


def list_input_devices() -> None:
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            print(f"[{idx}] {dev['name']}  (canales={dev['max_input_channels']}, "
                  f"rate={int(dev['default_samplerate'])})")


def find_device(name_or_index: str) -> int:
    if name_or_index == "":
        default_input = sd.default.device[0]
        if default_input is None or default_input < 0:
            raise RuntimeError("No hay dispositivo de entrada por defecto.")
        return int(default_input)
    if name_or_index.isdigit():
        return int(name_or_index)
    for idx, dev in enumerate(sd.query_devices()):
        if name_or_index.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            return idx
    raise RuntimeError(
        f"No se encontró un dispositivo de entrada que contenga '{name_or_index}'. "
        "Ejecuta `python main.py --list-devices` para ver los disponibles."
    )


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Remuestreo lineal: suficiente para voz y sin dependencias extra."""
    if src_rate == dst_rate or audio.size == 0:
        return audio
    duration = audio.size / src_rate
    n_out = int(round(duration * dst_rate))
    x_old = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class AudioCapturer:
    """Abre un InputStream y entrega bloques mono de 16 kHz a una cola. Se puede encender y apagar."""

    def __init__(self, device: str, target_rate: int = 16_000, block_seconds: float = 0.1):
        self.device_index = find_device(device)
        info = sd.query_devices(self.device_index)
        self.device_name = info["name"]
        self.src_rate = int(info["default_samplerate"])
        self.channels = min(2, info["max_input_channels"])
        self.target_rate = target_rate
        self.blocksize = int(self.src_rate * block_seconds)
        self.queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=600)
        self._stream: sd.InputStream | None = None
        self.started_at = 0.0
        self.last_signal_at = 0.0  # último bloque que no fue silencio digital absoluto

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}")
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        if np.any(mono):
            self.last_signal_at = time.time()
        chunk = resample(mono.astype(np.float32), self.src_rate, self.target_rate)
        try:
            self.queue.put_nowait(chunk)
        except queue.Full:
            pass  # el transcriptor va atrasado; descartamos para no acumular latencia

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.src_rate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self.started_at = time.time()
        print(f"[audio] Capturando de '{self.device_name}' a {self.src_rate} Hz → {self.target_rate} Hz")

    def seconds_of_digital_silence(self) -> float:
        """Segundos seguidos recibiendo ceros exactos (sin permiso de micrófono o nada sonando)."""
        if not self.running:
            return 0.0
        return time.time() - max(self.last_signal_at, self.started_at)

    def stop(self) -> None:
        """Cierra el stream (en el mic, apaga el indicador naranja de macOS)."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            print(f"[audio] Detenido '{self.device_name}'")
