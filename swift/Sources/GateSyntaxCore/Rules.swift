import SwiftSyntax

/// The rules SwiftLint does not hold, read from the syntax tree:
///
/// - `try-optional`: every `try?`. It turns the error into nil, and nothing at the use site
///   says that nil means failure.
/// - `unchecked-sendable`: `@unchecked Sendable` switches off the compiler's data-race check.
/// - `nonisolated-unsafe`: `nonisolated(unsafe)` does the same for one declaration.
/// - `view-body-length`: a SwiftUI view's `body` past its limit, counting lines that hold
///   code, as SwiftLint counts a function's.
final class RuleVisitor: SyntaxVisitor {
    private let source: Source
    private let viewBodyLimit: Int
    private var findings: [Finding] = []

    init(_ source: Source, viewBodyLimit: Int) {
        self.source = source
        self.viewBodyLimit = viewBodyLimit
        super.init(viewMode: .sourceAccurate)
    }

    func collect() -> [Finding] {
        walk(source.tree)
        return findings
    }

    private func report(_ rule: String, _ node: some SyntaxProtocol, _ message: String) {
        findings.append(Finding(rule: rule, line: source.firstLine(node), message: message))
    }

    override func visit(_ node: TryExprSyntax) -> SyntaxVisitorContinueKind {
        if node.questionOrExclamationMark?.tokenKind == .postfixQuestionMark {
            report(
                "try-optional", node,
                "try? discards the error: handle it with do/catch, or let it propagate"
            )
        }
        return .visitChildren
    }

    override func visit(_ node: AttributeSyntax) -> SyntaxVisitorContinueKind {
        if node.attributeName.trimmedDescription == "unchecked" {
            report(
                "unchecked-sendable", node,
                "@unchecked Sendable switches off the compiler's data-race checking"
            )
        }
        return .visitChildren
    }

    override func visit(_ node: DeclModifierSyntax) -> SyntaxVisitorContinueKind {
        if node.name.text == "nonisolated", node.detail?.detail.text == "unsafe" {
            report(
                "nonisolated-unsafe", node,
                "nonisolated(unsafe) switches off the compiler's data-race checking"
            )
        }
        return .visitChildren
    }

    override func visit(_ node: PatternBindingSyntax) -> SyntaxVisitorContinueKind {
        guard Self.isViewBody(node), let accessors = node.accessorBlock else {
            return .visitChildren
        }
        let lines = codeLines(inside: accessors)
        if lines > viewBodyLimit {
            report(
                "view-body-length", node,
                "A view's body spans \(lines) lines of code (\(lines) > \(viewBodyLimit))"
            )
        }
        return .visitChildren
    }

    private static func isViewBody(_ node: PatternBindingSyntax) -> Bool {
        let type = node.typeAnnotation?.type.trimmedDescription
        return node.pattern.trimmedDescription == "body" && type == "some View"
    }

    /// The lines between the braces of `block` that hold a token.
    private func codeLines(inside block: AccessorBlockSyntax) -> Int {
        let braces = [block.leftBrace.id, block.rightBrace.id]
        let tokens = block.tokens(viewMode: .sourceAccurate).filter { !braces.contains($0.id) }
        return Set(tokens.map { source.firstLine($0) }).count
    }
}
