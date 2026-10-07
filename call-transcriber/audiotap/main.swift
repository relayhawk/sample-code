// audiotap: streams the Mac's audio output (what plays out of the speakers or headphones)
// to stdout as raw 16-bit mono PCM at the tap's sample rate (printed to stderr), using a
// Core Audio process tap (macOS 14.2+). The tap delivers nothing while the Mac is silent,
// so silence is padded in to keep the stream real-time.
// Usage: audiotap [--app <bundle-id-prefix>]   (default: everything the Mac plays)
import AudioToolbox
import CoreAudio
import Foundation

func log(_ s: String) { FileHandle.standardError.write(("audiotap: " + s + "\n").data(using: .utf8)!) }

func fail(_ s: String, _ st: OSStatus) -> Never { log("\(s) failed (\(st))"); exit(1) }

func address(_ sel: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
}

func readArray(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> [AudioObjectID] {
    var addr = address(sel)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(obj, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func readString(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    var addr = address(sel)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var value: Unmanaged<CFString>?
    guard AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, &value) == noErr, let v = value else { return nil }
    return v.takeRetainedValue() as String
}

var appPrefix: String?
let args = CommandLine.arguments
if let i = args.firstIndex(of: "--app"), i + 1 < args.count { appPrefix = args[i + 1] }

let tapDesc: CATapDescription
if let prefix = appPrefix {
    var procs: [AudioObjectID] = []
    while procs.isEmpty {
        procs = readArray(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyProcessObjectList)
            .filter { (readString($0, kAudioProcessPropertyBundleID) ?? "").hasPrefix(prefix) }
        if procs.isEmpty { log("waiting for an app matching \(prefix) to play audio"); sleep(2) }
    }
    log("tapping \(procs.count) process(es) matching \(prefix)")
    tapDesc = CATapDescription(stereoMixdownOfProcesses: procs)
} else {
    log("tapping all system audio output")
    tapDesc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
}
tapDesc.uuid = UUID()
tapDesc.muteBehavior = .unmuted
tapDesc.isPrivate = true
tapDesc.name = "call-transcriber-tap"

var tapID = AudioObjectID(kAudioObjectUnknown)
var st = AudioHardwareCreateProcessTap(tapDesc, &tapID)
if st != noErr { fail("AudioHardwareCreateProcessTap", st) }

var fmtAddr = address(kAudioTapPropertyFormat)
var asbd = AudioStreamBasicDescription()
var asbdSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
st = AudioObjectGetPropertyData(tapID, &fmtAddr, 0, nil, &asbdSize, &asbd)
if st != noErr { fail("read tap format", st) }
let interleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved == 0
let isFloat = asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0
log("format rate=\(Int(asbd.mSampleRate)) channels=\(asbd.mChannelsPerFrame) bits=\(asbd.mBitsPerChannel) float=\(isFloat) interleaved=\(interleaved)")
guard isFloat, asbd.mBitsPerChannel == 32 else { log("unsupported tap format"); exit(1) }

var outID = AudioObjectID(kAudioObjectUnknown)
var outAddr = address(kAudioHardwarePropertyDefaultSystemOutputDevice)
var outSize = UInt32(MemoryLayout<AudioObjectID>.size)
AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &outAddr, 0, nil, &outSize, &outID)
guard let outUID = readString(outID, kAudioDevicePropertyDeviceUID) else { log("no default output device"); exit(1) }

let aggDesc: [String: Any] = [
    kAudioAggregateDeviceNameKey: "call-transcriber-aggregate",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true, kAudioSubTapUIDKey: tapDesc.uuid.uuidString]],
]
var aggID = AudioObjectID(kAudioObjectUnknown)
st = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggID)
if st != noErr { fail("AudioHardwareCreateAggregateDevice", st) }

func cleanup() {
    AudioHardwareDestroyAggregateDevice(aggID)
    AudioHardwareDestroyProcessTap(tapID)
}

let rate = asbd.mSampleRate
let lock = NSLock()
var written = 0  // frames written so far
var lastIO = Date.distantPast
var startedAt = Date()

func emit(_ samples: UnsafeBufferPointer<Int16>) {
    _ = samples.withMemoryRebound(to: UInt8.self) { write(1, $0.baseAddress, $0.count) }
    written += samples.count
}

var procID: AudioDeviceIOProcID?
st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, nil) { _, inData, _, _, _ in
    let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
    guard let first = abl.first, first.mData != nil else { return }
    // Mix every channel down to mono int16.
    let chans = interleaved ? Int(first.mNumberChannels) : abl.count
    let frames = Int(first.mDataByteSize) / 4 / (interleaved ? max(chans, 1) : 1)
    var out = [Int16](repeating: 0, count: frames)
    for f in 0..<frames {
        var sum: Float = 0
        for c in 0..<chans {
            if interleaved {
                sum += first.mData!.assumingMemoryBound(to: Float.self)[f * chans + c]
            } else if let p = abl[c].mData {
                sum += p.assumingMemoryBound(to: Float.self)[f]
            }
        }
        let v = max(-1, min(1, sum / Float(max(chans, 1))))
        out[f] = Int16(v * 32767)
    }
    lock.lock()
    lastIO = Date()
    out.withUnsafeBufferPointer { emit($0) }
    lock.unlock()
}
if st != noErr { cleanup(); fail("AudioDeviceCreateIOProcIDWithBlock", st) }
st = AudioDeviceStart(aggID, procID)
if st != noErr { cleanup(); fail("AudioDeviceStart", st) }
startedAt = Date()
log("streaming rate=\(Int(rate))")

// While nothing plays, pad silence up to ~40 ms behind real time.
let silence = [Int16](repeating: 0, count: Int(rate))
let padTimer = DispatchSource.makeTimerSource(queue: DispatchQueue(label: "pad"))
padTimer.schedule(deadline: .now() + 0.02, repeating: 0.02)
padTimer.setEventHandler {
    lock.lock()
    defer { lock.unlock() }
    guard Date().timeIntervalSince(lastIO) > 0.06 else { return }
    let target = Int((Date().timeIntervalSince(startedAt) - 0.04) * rate)
    var missing = target - written
    while missing > 0 {
        let n = min(missing, silence.count)
        silence.withUnsafeBufferPointer { emit(UnsafeBufferPointer(rebasing: $0[0..<n])) }
        missing -= n
    }
}
padTimer.resume()

signal(SIGPIPE, SIG_IGN)
var sources: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT, SIGHUP] {
    signal(sig, SIG_IGN)
    let s = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    s.setEventHandler {
        AudioDeviceStop(aggID, procID)
        if let p = procID { AudioDeviceDestroyIOProcID(aggID, p) }
        cleanup()
        log("stopped")
        exit(0)
    }
    s.resume()
    sources.append(s)
}
dispatchMain()
