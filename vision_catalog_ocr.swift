import AppKit
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count > 1,
      let document = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    fputs("Usage: vision_catalog_ocr PDF\n", stderr)
    exit(2)
}

var rows: [[String: Any]] = []
for pageIndex in 0..<document.pageCount {
    guard let page = document.page(at: pageIndex) else { continue }
    let image = page.thumbnail(of: NSSize(width: 1530, height: 1980), for: .mediaBox)
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { continue }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["es-MX", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])

    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let box = observation.boundingBox
        rows.append([
            "page": pageIndex + 1,
            "text": candidate.string,
            "confidence": candidate.confidence,
            "x": box.origin.x,
            "y": box.origin.y,
            "width": box.width,
            "height": box.height,
        ])
    }
}

let data = try JSONSerialization.data(withJSONObject: rows)
FileHandle.standardOutput.write(data)
