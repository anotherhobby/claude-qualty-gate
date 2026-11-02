import SwiftParser
import SwiftSyntax

/// A named stretch of the file that findings are placed by: a type, a function, an
/// initializer, an accessor or a closure. Lines are 1-based and inclusive.
public struct Span: Codable, Equatable, Sendable {
    public let name: String
    public let first: Int
    public let last: Int

    public init(name: String, first: Int, last: Int) {
        self.name = name
        self.first = first
        self.last = last
    }
}

/// One finding, in the shape the gate reads.
public struct Finding: Codable, Equatable, Sendable {
    public let rule: String
    public let line: Int
    public let message: String

    public init(rule: String, line: Int, message: String) {
        self.rule = rule
        self.line = line
        self.message = message
    }
}

/// The limits the gate holds the measured rules to.
public struct Limits: Sendable {
    public let cognitive: Int
    public let viewBody: Int

    public init(cognitive: Int, viewBody: Int) {
        self.cognitive = cognitive
        self.viewBody = viewBody
    }
}

/// Everything the gate asks of one file.
public struct Report: Codable, Equatable, Sendable {
    public let spans: [Span]
    public let findings: [Finding]
}

/// One parsed file, with what turns a position into a line.
struct Source {
    let tree: SourceFileSyntax
    let converter: SourceLocationConverter

    init(_ text: String) {
        tree = Parser.parse(source: text)
        converter = SourceLocationConverter(fileName: "", tree: tree)
    }

    func line(_ position: AbsolutePosition) -> Int {
        converter.location(for: position).line
    }

    /// The first line of `node`, its attributes included and its leading comments not.
    func firstLine(_ node: some SyntaxProtocol) -> Int {
        line(node.positionAfterSkippingLeadingTrivia)
    }

    func lastLine(_ node: some SyntaxProtocol) -> Int {
        line(node.endPositionBeforeTrailingTrivia)
    }
}

/// Every span in `text`, for the copy of a file at HEAD.
public func spans(of text: String) -> [Span] {
    let collector = SpanCollector(Source(text), limit: nil)
    return collector.collect()
}

/// Spans and findings for `text`, measured against `limits`.
public func check(_ text: String, limits: Limits) -> Report {
    let source = Source(text)
    let collector = SpanCollector(source, limit: limits.cognitive)
    let spans = collector.collect()
    let rules = RuleVisitor(source, viewBodyLimit: limits.viewBody)
    let findings = collector.findings + rules.collect() + Comments(source).findings()
    let sorted = findings.sorted { ($0.line, $0.rule) < ($1.line, $1.rule) }
    return Report(spans: spans, findings: sorted)
}
