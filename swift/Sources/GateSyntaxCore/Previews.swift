import SwiftSyntax

/// The lines each `#Preview` block covers: sample values for a preview, which the magic-numbers
/// rule does not hold, as it does not hold a test's.
final class PreviewCollector: SyntaxVisitor {
    private let source: Source
    private var found: [LineRange] = []

    init(_ source: Source) {
        self.source = source
        super.init(viewMode: .sourceAccurate)
    }

    func collect() -> [LineRange] {
        walk(source.tree)
        return found
    }

    // A freestanding macro at a file's top level parses as a declaration or, among statements,
    // as an expression; `#Preview` may be either.
    override func visit(_ node: MacroExpansionDeclSyntax) -> SyntaxVisitorContinueKind {
        record(node, named: node.macroName.text)
    }

    override func visit(_ node: MacroExpansionExprSyntax) -> SyntaxVisitorContinueKind {
        record(node, named: node.macroName.text)
    }

    private func record(_ node: some SyntaxProtocol, named name: String)
        -> SyntaxVisitorContinueKind
    {
        guard name == "Preview" else {
            return .visitChildren
        }
        found.append(LineRange(first: source.firstLine(node), last: source.lastLine(node)))
        return .skipChildren
    }
}

/// The lines `first` to `last` of a file, both included.
public struct LineRange: Codable, Equatable, Sendable {
    public let first: Int
    public let last: Int

    public init(first: Int, last: Int) {
        self.first = first
        self.last = last
    }
}

/// The lines of every `#Preview` block in `text`.
public func previews(of text: String) -> [LineRange] {
    PreviewCollector(Source(text)).collect()
}
