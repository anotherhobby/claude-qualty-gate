import SwiftSyntax

/// Cognitive complexity, after SonarSource's definition (G. Ann Campbell, "Cognitive
/// Complexity", 2017), mapped onto Swift:
///
/// - `if`, `guard`, `switch`, `for`, `while`, `repeat`, `catch` and the ternary operator
///   each add one, plus one for every level they are nested in.
/// - `else if` and `else` add one each, with nothing for nesting.
/// - Each run of like logical operators adds one: `a && b && c` is one, `a && b || c` two.
/// - A labelled `break` or `continue` adds one.
///
/// Closures, nested functions and local types are functions of their own, measured apart
/// with their nesting starting again at zero. Recursion, which the definition also counts,
/// is not counted here.
enum Cognitive {
    static func score(_ body: Syntax) -> Int {
        var counter = Counter()
        counter.children(of: body, nesting: 0)
        return counter.total
    }
}

private struct Counter {
    private(set) var total = 0

    mutating func children(of node: Syntax, nesting: Int) {
        for child in node.children(viewMode: .sourceAccurate) {
            visit(child, nesting: nesting)
        }
    }

    private mutating func visit(_ node: Syntax, nesting: Int) {
        switch node.as(SyntaxEnum.self) {
        case .ifExpr(let branch):
            ifChain(branch, nesting: nesting, isElseIf: false)
        case .guardStmt, .switchExpr, .forStmt, .whileStmt, .repeatStmt, .catchClause,
            .unresolvedTernaryExpr:
            total += 1 + nesting
            children(of: node, nesting: nesting + 1)
        case .sequenceExpr(let sequence):
            total += Self.logicalRuns(sequence)
            children(of: node, nesting: nesting)
        case .breakStmt(let jump) where jump.label != nil:
            total += 1
        case .continueStmt(let jump) where jump.label != nil:
            total += 1
        case .closureExpr, .functionDecl, .initializerDecl, .deinitializerDecl, .subscriptDecl,
            .structDecl, .classDecl, .enumDecl, .actorDecl, .protocolDecl, .extensionDecl:
            return
        case .patternBinding(let binding) where binding.accessorBlock != nil:
            return
        default:
            children(of: node, nesting: nesting)
        }
    }

    private mutating func ifChain(_ branch: IfExprSyntax, nesting: Int, isElseIf: Bool) {
        total += isElseIf ? 1 : 1 + nesting
        children(of: Syntax(branch.conditions), nesting: nesting + 1)
        children(of: Syntax(branch.body), nesting: nesting + 1)
        switch branch.elseBody {
        case .ifExpr(let next):
            ifChain(next, nesting: nesting, isElseIf: true)
        case .codeBlock(let block):
            total += 1
            children(of: Syntax(block), nesting: nesting + 1)
        case nil:
            break
        }
    }

    /// One for each run of like logical operators in an unfolded sequence.
    private static func logicalRuns(_ sequence: SequenceExprSyntax) -> Int {
        let logical = sequence.elements.compactMap { element -> String? in
            let text = element.as(BinaryOperatorExprSyntax.self)?.operator.text
            return text == "&&" || text == "||" ? text : nil
        }
        let previous: [String?] = [nil] + logical.map(Optional.some)
        return zip(logical, previous).filter { $0 != $1 }.count
    }
}
