import SwiftSyntax

/// Collects every span, named through the types around it, and measures the cognitive
/// complexity of each function-like one when given a limit.
///
/// Functions are named with their argument labels, `Type.move(to:by:)`, so overloads stay
/// apart. A closure is its own function, named by its place in its enclosing one,
/// `Type.body.{closure#2}`, and is measured apart from it.
final class SpanCollector: SyntaxVisitor {
    private let source: Source
    private let limit: Int?
    private var found: [Span] = []
    private(set) var findings: [Finding] = []
    // The enclosing names, each with the closures counted in it so far.
    private var scopes: [(name: String, closures: Int)] = [("", 0)]

    init(_ source: Source, limit: Int?) {
        self.source = source
        self.limit = limit
        super.init(viewMode: .sourceAccurate)
    }

    func collect() -> [Span] {
        walk(source.tree)
        return found
    }

    private func qualified(_ name: String) -> String {
        let prefix = scopes.last?.name ?? ""
        return prefix.isEmpty ? name : "\(prefix).\(name)"
    }

    /// Records `node` as a span and opens its scope; measures `body` when there is one.
    private func open(_ name: String, _ node: some SyntaxProtocol, body: Syntax?) {
        let span = Span(
            name: qualified(name),
            first: source.firstLine(node),
            last: source.lastLine(node)
        )
        found.append(span)
        scopes.append((span.name, 0))
        if let limit, let body {
            measure(span, body: body, limit: limit)
        }
    }

    private func measure(_ span: Span, body: Syntax, limit: Int) {
        let score = Cognitive.score(body)
        guard score > limit else {
            return
        }
        let message = "\(span.name) has cognitive complexity \(score) (limit \(limit))"
        findings.append(Finding(rule: "cognitive-complexity", line: span.first, message: message))
    }

    private func close() {
        scopes.removeLast()
    }

    private static func labels(_ parameters: FunctionParameterListSyntax) -> String {
        parameters.map { "\($0.firstName.text):" }.joined()
    }

    // MARK: Types

    override func visit(_ node: StructDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.name.text, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: StructDeclSyntax) { close() }

    override func visit(_ node: ClassDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.name.text, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: ClassDeclSyntax) { close() }

    override func visit(_ node: EnumDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.name.text, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: EnumDeclSyntax) { close() }

    override func visit(_ node: ActorDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.name.text, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: ActorDeclSyntax) { close() }

    override func visit(_ node: ProtocolDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.name.text, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: ProtocolDeclSyntax) { close() }

    override func visit(_ node: ExtensionDeclSyntax) -> SyntaxVisitorContinueKind {
        open(node.extendedType.trimmedDescription, node, body: nil)
        return .visitChildren
    }

    override func visitPost(_: ExtensionDeclSyntax) { close() }

    // MARK: Functions

    override func visit(_ node: FunctionDeclSyntax) -> SyntaxVisitorContinueKind {
        let name = "\(node.name.text)(\(Self.labels(node.signature.parameterClause.parameters)))"
        open(name, node, body: node.body.map(Syntax.init))
        return .visitChildren
    }

    override func visitPost(_: FunctionDeclSyntax) { close() }

    override func visit(_ node: InitializerDeclSyntax) -> SyntaxVisitorContinueKind {
        let name = "init(\(Self.labels(node.signature.parameterClause.parameters)))"
        open(name, node, body: node.body.map(Syntax.init))
        return .visitChildren
    }

    override func visitPost(_: InitializerDeclSyntax) { close() }

    override func visit(_ node: DeinitializerDeclSyntax) -> SyntaxVisitorContinueKind {
        open("deinit", node, body: node.body.map(Syntax.init))
        return .visitChildren
    }

    override func visitPost(_: DeinitializerDeclSyntax) { close() }

    override func visit(_ node: SubscriptDeclSyntax) -> SyntaxVisitorContinueKind {
        let name = "subscript(\(Self.labels(node.parameterClause.parameters)))"
        open(name, node, body: node.accessorBlock.map(Syntax.init))
        return .visitChildren
    }

    override func visitPost(_: SubscriptDeclSyntax) { close() }

    /// A computed property, or one with observers, is a function of its own. A stored one
    /// is not, but names the closures in its initializer.
    override func visit(_ node: PatternBindingSyntax) -> SyntaxVisitorContinueKind {
        guard let accessors = node.accessorBlock else {
            scopes.append((qualified(node.pattern.trimmedDescription), 0))
            return .visitChildren
        }
        open(node.pattern.trimmedDescription, node, body: Syntax(accessors))
        return .visitChildren
    }

    override func visitPost(_: PatternBindingSyntax) { close() }

    override func visit(_ node: ClosureExprSyntax) -> SyntaxVisitorContinueKind {
        let count = (scopes.last?.closures ?? 0) + 1
        scopes[scopes.count - 1].closures = count
        open("{closure#\(count)}", node, body: Syntax(node.statements))
        return .visitChildren
    }

    override func visitPost(_: ClosureExprSyntax) { close() }
}
