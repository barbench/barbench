// Apple Vision's barcode detector, for barbench's `vision` adapter
// (barbench/adapters.py), which builds this file with `swiftc` on macOS.
//
//   vision [--symbology NAME] PATH...
//                     one JSON line per file, in order:
//                     {"codes": [{"text": ..., "bytes": ...}]} or {"error": ...}
//   vision --version  the detector's revision, the macOS build and the chip
//
// It asks for one symbology only: Code 128, as every adapter does in
// `barbench run`, unless --symbology names `qr` or `datamatrix` (for
// `barbench time` on such a corpus). It pins the request's revision: a
// newer default would otherwise change the numbers with the OS.
// Files are binary 8-bit PGM, as barbench/pgm.py writes them.

import CoreGraphics
import Darwin
import Foundation
import Vision

/// The revision of VNDetectBarcodesRequest this adapter measures. Revision 4
/// came with macOS 14; a change here changes every Vision number.
let revision = VNDetectBarcodesRequestRevision4

struct Failure: Error {
    let message: String
}

func isSpace(_ byte: UInt8) -> Bool {
    byte == 32 || (9...13).contains(byte)
}

/// Reads a binary 8-bit PGM, with the same header rules as barbench/pgm.py.
func loadPGM(_ path: String) throws -> CGImage {
    guard let data = FileManager.default.contents(atPath: path) else {
        throw Failure(message: "\(path): cannot be read")
    }
    let bytes = [UInt8](data)
    var at = 0
    var fields: [String] = []
    while fields.count < 4 {
        while at < bytes.count && isSpace(bytes[at]) { at += 1 }
        let start = at
        while at < bytes.count && !isSpace(bytes[at]) { at += 1 }
        fields.append(String(decoding: bytes[start..<at], as: UTF8.self))
    }
    guard fields[0] == "P5", fields[3] == "255",
          let width = Int(fields[1]), let height = Int(fields[2]), width > 0, height > 0
    else {
        throw Failure(message: "\(path): not an 8-bit binary PGM")
    }
    let (size, overflow) = width.multipliedReportingOverflow(by: height)
    let start = at + 1
    guard !overflow, start <= bytes.count, bytes.count - start >= size else {
        throw Failure(message: "\(path): truncated")
    }
    let raster = Data(bytes[start..<(start + size)])
    guard let provider = CGDataProvider(data: raster as CFData),
          let image = CGImage(
              width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8,
              bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
              bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
              provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
    else {
        throw Failure(message: "\(path): no image could be made of it")
    }
    return image
}

/// The codes Vision finds in one image: their text, and their payload in hex
/// (for Code 128, Vision's `payloadData` holds the symbol's codewords, start,
/// check and stop included), which the adapter reports when Vision gives no text,
/// so that a misread of them still counts.
func read(_ path: String, _ symbology: VNBarcodeSymbology) throws -> [[String: Any]] {
    let image = try loadPGM(path)
    let request = VNDetectBarcodesRequest()
    request.revision = revision
    request.symbologies = [symbology]
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).map { found in
        let hex = found.payloadData.map { $0.map { String(format: "%02x", $0) }.joined() }
        return ["text": found.payloadStringValue ?? NSNull(), "bytes": hex ?? NSNull()]
    }
}

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
        print(String(decoding: data, as: UTF8.self))
    } else {
        print(#"{"error": "the result could not be written as JSON"}"#)
    }
}

/// The chip, such as "Apple M3": Vision may run on its Neural Engine or GPU,
/// so a report says which machine its numbers come from.
func chip() -> String {
    var size = 0
    guard sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0) == 0, size > 0 else {
        return "unknown chip"
    }
    var name = [CChar](repeating: 0, count: size)
    guard sysctlbyname("machdep.cpu.brand_string", &name, &size, nil, 0) == 0 else {
        return "unknown chip"
    }
    return String(decoding: name.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
}

/// The symbologies --symbology may name, as barbench names them.
let symbologies: [String: VNBarcodeSymbology] = ["code128": .code128, "qr": .qr, "datamatrix": .dataMatrix]

var arguments = Array(CommandLine.arguments.dropFirst())
var symbology = VNBarcodeSymbology.code128
if arguments.first == "--symbology" {
    guard arguments.count >= 2, let chosen = symbologies[arguments[1]] else {
        FileHandle.standardError.write(Data("--symbology needs one of code128, qr, datamatrix\n".utf8))
        exit(2)
    }
    symbology = chosen
    arguments.removeFirst(2)
}
guard VNDetectBarcodesRequest.supportedRevisions.contains(revision) else {
    FileHandle.standardError.write(
        Data("VNDetectBarcodesRequest revision \(revision) is not supported here\n".utf8))
    exit(3)
}
if arguments == ["--version"] {
    let os = ProcessInfo.processInfo.operatingSystemVersionString
    print("Apple Vision barcodes revision \(revision), macOS \(os), \(chip())")
    exit(0)
}
for path in arguments {
    do {
        emit(["codes": try read(path, symbology)])
    } catch let failure as Failure {
        emit(["error": failure.message])
    } catch {
        emit(["error": "\(path): \(error.localizedDescription)"])
    }
}
