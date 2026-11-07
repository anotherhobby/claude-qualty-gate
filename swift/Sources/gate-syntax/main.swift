// gate-syntax: the quality gate's view of one Swift file, as JSON on stdout.
//
//     gate-syntax check COGNITIVE_LIMIT VIEW_BODY_LIMIT FILE   spans and findings
//     gate-syntax spans                                        spans of the source on stdin
//     gate-syntax previews                                     the lines of each `#Preview` block
import Foundation
import GateSyntaxCore

private let usageError: Int32 = 2

private func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(usageError)
}

private func emit(_ value: some Encodable) {
    let encoder = JSONEncoder()
    encoder.outputFormatting = .sortedKeys
    do {
        FileHandle.standardOutput.write(try encoder.encode(value))
    } catch {
        fail("gate-syntax: \(error)")
    }
}

private func read(_ path: String) -> String {
    do {
        return try String(contentsOfFile: path, encoding: .utf8)
    } catch {
        fail("gate-syntax: cannot read \(path): \(error.localizedDescription)")
    }
}

/// `check`'s arguments, or nil when they are not two limits and a file.
private func checkArguments(_ arguments: ArraySlice<String>) -> (Limits, String)? {
    var rest = arguments.makeIterator()
    guard let cognitive = rest.next().flatMap({ Int($0) }),
        let viewBody = rest.next().flatMap({ Int($0) }),
        let path = rest.next(),
        rest.next() == nil
    else {
        return nil
    }
    return (Limits(cognitive: cognitive, viewBody: viewBody), path)
}

/// The source on standard input.
private func standardInput() -> String {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let text = String(bytes: input, encoding: .utf8) else {
        fail("gate-syntax: the source on stdin is not UTF-8")
    }
    return text
}

private func run(_ arguments: [String]) {
    switch arguments.first {
    case "check":
        guard let (limits, path) = checkArguments(arguments.dropFirst()) else {
            fail("usage: gate-syntax check COGNITIVE_LIMIT VIEW_BODY_LIMIT FILE")
        }
        emit(check(read(path), limits: limits))
    case "spans":
        emit(spans(of: standardInput()))
    case "previews":
        emit(previews(of: standardInput()))
    default:
        fail(
            "usage: gate-syntax check COGNITIVE_LIMIT VIEW_BODY_LIMIT FILE | gate-syntax spans"
                + " | gate-syntax previews")
    }
}

run(Array(CommandLine.arguments.dropFirst()))
