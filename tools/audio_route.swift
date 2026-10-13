// Crea un "Dispositivo de salida múltiple" (tu salida actual + BlackHole) y lo pone como salida del
// sistema, para que Chrome/Zoom suenen en tus audífonos y a la vez lleguen al copiloto.
//
//   swift tools/audio_route.swift on    # activa (lo crea si no existe)
//   swift tools/audio_route.swift off   # vuelve a tu salida normal
//   swift tools/audio_route.swift       # muestra el estado
import CoreAudio
import Foundation

let aggregateUID = "com.meetingcopilot.multioutput"
let aggregateName = "Copilot (salida + BlackHole)"

func getProperty<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                    scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ value: inout T) -> OSStatus {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value)
}

func stringProperty(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var value: Unmanaged<CFString>?
    guard getProperty(object, selector, &value) == noErr, let v = value else { return "" }
    return v.takeRetainedValue() as String
}

func allDevices() -> [AudioObjectID] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size)
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids)
    return ids
}

func name(_ id: AudioObjectID) -> String { stringProperty(id, kAudioObjectPropertyName) }
func uid(_ id: AudioObjectID) -> String { stringProperty(id, kAudioDevicePropertyDeviceUID) }
func device(withUID u: String) -> AudioObjectID? { allDevices().first { uid($0) == u } }

func defaultOutput() -> AudioObjectID {
    var id = AudioObjectID(0)
    _ = getProperty(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice, &id)
    return id
}

func setDefaultOutput(_ id: AudioObjectID) {
    for selector in [kAudioHardwarePropertyDefaultOutputDevice, kAudioHardwarePropertyDefaultSystemOutputDevice] {
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        var value = id
        AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil,
                                   UInt32(MemoryLayout<AudioObjectID>.size), &value)
    }
}

/// UID de la salida "real" guardada dentro del dispositivo múltiple (su reloj principal).
func mainSubdeviceUID(of aggregate: AudioObjectID) -> String {
    stringProperty(aggregate, kAudioAggregateDevicePropertyMainSubDevice)
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let command = CommandLine.arguments.dropFirst().first ?? "status"
let current = defaultOutput()

switch command {
case "on":
    if uid(current) == aggregateUID {
        print("Ya activo: la salida es '\(name(current))'.")
        exit(0)
    }
    guard let blackhole = allDevices().first(where: { name($0).contains("BlackHole") }) else {
        fail("No encontré BlackHole. Instálalo con: brew install blackhole-2ch")
    }
    if current == blackhole { fail("Tu salida actual es BlackHole; elige primero tus audífonos o bocinas.") }

    // Si quedó uno de antes con otra salida, se recrea con la salida actual.
    if let old = device(withUID: aggregateUID) { AudioHardwareDestroyAggregateDevice(old) }

    let mainUID = uid(current)
    let description: [String: Any] = [
        kAudioAggregateDeviceNameKey: aggregateName,
        kAudioAggregateDeviceUIDKey: aggregateUID,
        kAudioAggregateDeviceIsStackedKey: 1,  // 1 = salida múltiple (no agregado)
        kAudioAggregateDeviceMainSubDeviceKey: mainUID,
        kAudioAggregateDeviceSubDeviceListKey: [
            [kAudioSubDeviceUIDKey: mainUID],
            [kAudioSubDeviceUIDKey: uid(blackhole), kAudioSubDeviceDriftCompensationKey: 1],
        ],
    ]
    var aggregate = AudioObjectID(0)
    let status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregate)
    guard status == noErr else { fail("CoreAudio no pudo crear el dispositivo (error \(status)).") }
    Thread.sleep(forTimeInterval: 0.5)  // CoreAudio tarda un poco en publicarlo
    setDefaultOutput(aggregate)
    print("Activo: '\(name(current))' + BlackHole → salida del sistema '\(aggregateName)'.")
    print("Nota: con este dispositivo el volumen se ajusta en la app de la llamada, no con las teclas.")

case "off":
    guard let aggregate = device(withUID: aggregateUID) else {
        print("No hay dispositivo del copiloto; la salida es '\(name(current))'.")
        exit(0)
    }
    let mainUID = mainSubdeviceUID(of: aggregate)
    if uid(current) == aggregateUID, let real = device(withUID: mainUID) {
        setDefaultOutput(real)
    }
    AudioHardwareDestroyAggregateDevice(aggregate)
    print("Desactivado. Salida: '\(name(defaultOutput()))'.")

default:
    let active = uid(current) == aggregateUID
    print("Salida actual: '\(name(current))' — copiloto \(active ? "RECIBE" : "NO recibe") el audio del sistema.")
}
