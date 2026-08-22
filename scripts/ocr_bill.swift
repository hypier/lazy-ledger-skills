import Foundation
import Vision
import AppKit
import CoreGraphics

struct OCRItem: Codable {
    let text: String
    let x: Double
    let y: Double
    let w: Double
    let h: Double
    let confidence: Double
    let global_y: Double
    let slice: Int
}

struct OCRResult: Codable {
    let width: Int
    let height: Int
    let slice_count: Int
    let items: [OCRItem]
}

let path = CommandLine.arguments[1]
let sliceHeightArg = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2]) : nil
let overlapArg = CommandLine.arguments.count > 3 ? Int(CommandLine.arguments[3]) : nil

guard let img = NSImage(contentsOfFile: path),
      let tiff = img.tiffRepresentation,
      let bmp = NSBitmapImageRep(data: tiff),
      let full = bmp.cgImage else {
    fputs("failed to load image\n", stderr)
    exit(1)
}

let width = full.width
let height = full.height
let sliceH = min(sliceHeightArg ?? 1400, height)
let overlap = overlapArg ?? 120
var items: [OCRItem] = []
var sliceIndex = 0
var yTop = 0

func ocr(_ image: CGImage, slice: Int, yTop: Int, slicePixelHeight: Int) throws {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([req])
    for obs in req.results ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        let b = obs.boundingBox
        let globalY = Double(yTop) + (1.0 - Double(b.origin.y) - Double(b.size.height)) * Double(slicePixelHeight)
        items.append(
            OCRItem(
                text: cand.string,
                x: Double(b.origin.x),
                y: Double(b.origin.y),
                w: Double(b.size.width),
                h: Double(b.size.height),
                confidence: Double(cand.confidence),
                global_y: globalY,
                slice: slice
            )
        )
    }
}

while yTop < height {
    let thisH = min(sliceH, height - yTop)
    // CGImage.cropping(to:) uses top-left origin on the bitmap.
    let rect = CGRect(x: 0, y: yTop, width: width, height: thisH)
    guard let cropped = full.cropping(to: rect) else {
        fputs("failed to crop slice \(sliceIndex)\n", stderr)
        exit(1)
    }
    do {
        try ocr(cropped, slice: sliceIndex, yTop: yTop, slicePixelHeight: thisH)
    } catch {
        fputs("ocr failed on slice \(sliceIndex): \(error)\n", stderr)
        exit(1)
    }
    if yTop + thisH >= height {
        break
    }
    yTop += thisH - overlap
    sliceIndex += 1
}

let payload = OCRResult(width: width, height: height, slice_count: sliceIndex + 1, items: items)
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let data = try encoder.encode(payload)
FileHandle.standardOutput.write(data)
