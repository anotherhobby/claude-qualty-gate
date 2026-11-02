import SwiftParser
import SwiftSyntax

/// The rules read from comments:
///
/// - `swiftlint-disable`: a `swiftlint:disable` or `swiftlint:enable` comment. The gate
///   lints with them stripped, so none silences anything, and none is kept.
/// - `commented-out-code`: a run of whole-line `//` comments that parses as Swift and
///   holds more than a bare word. Doc comments (`///`) are prose and are left alone.
struct Comments {
    private struct LineComment {
        let line: Int
        // The text after the two slashes.
        let text: String
        // Nothing but whitespace before it on its line.
        let wholeLine: Bool

        init(_ line: Int, _ raw: String, wholeLine: Bool) {
            self.line = line
            text = String(raw.dropFirst("//".count))
            self.wholeLine = wholeLine
        }
    }

    private static let notCode: Set<SyntaxKind> = [
        .declReferenceExpr, .integerLiteralExpr, .floatLiteralExpr, .stringLiteralExpr,
        .booleanLiteralExpr, .nilLiteralExpr,
    ]

    private let source: Source

    init(_ source: Source) {
        self.source = source
    }

    func findings() -> [Finding] {
        let comments = lineComments()
        return disables(comments) + commentedOutCode(comments)
    }

    private func lineComments() -> [LineComment] {
        var found: [LineComment] = []
        for token in source.tree.tokens(viewMode: .sourceAccurate) {
            var position = token.position
            var wholeLine = token.previousToken(viewMode: .sourceAccurate) == nil
            for piece in token.leadingTrivia {
                if case .lineComment(let raw) = piece {
                    found.append(LineComment(source.line(position), raw, wholeLine: wholeLine))
                }
                wholeLine = piece.isNewline || (wholeLine && piece.isWhitespace)
                position += piece.sourceLength
            }
            let end = source.line(token.endPositionBeforeTrailingTrivia)
            for case .lineComment(let raw) in token.trailingTrivia {
                found.append(LineComment(end, raw, wholeLine: false))
            }
        }
        return found
    }

    private func disables(_ comments: [LineComment]) -> [Finding] {
        let markers = ["swiftlint:disable", "swiftlint:enable"]
        return comments.filter { comment in markers.contains { comment.text.contains($0) } }
            .map {
                Finding(
                    rule: "swiftlint-disable", line: $0.line,
                    message: "A swiftlint:disable comment silences nothing here: fix what it hides"
                )
            }
    }

    /// Runs of whole-line comments on consecutive lines that read as code.
    private func commentedOutCode(_ comments: [LineComment]) -> [Finding] {
        var runs: [[LineComment]] = []
        for comment in comments where comment.wholeLine && !comment.text.hasPrefix("/") {
            if let last = runs.last?.last, last.line + 1 == comment.line {
                runs[runs.count - 1].append(comment)
            } else {
                runs.append([comment])
            }
        }
        return runs.compactMap { run in
            guard let first = run.first, Self.isCode(run.map(\.text).joined(separator: "\n")) else {
                return nil
            }
            return Finding(
                rule: "commented-out-code", line: first.line,
                message: "Commented-out code: delete it, git keeps the history"
            )
        }
    }

    static func isCode(_ text: String) -> Bool {
        let tree = Parser.parse(source: text)
        return !tree.hasError && tree.statements.contains { isCode(Syntax($0.item)) }
    }

    /// Whether one parsed item is code rather than a word or a label: a bare name or a
    /// literal is not, and neither is `TODO:` or `Note:` before one, nor a hyphenated
    /// directive such as `swift-format-ignore`.
    private static func isCode(_ item: Syntax) -> Bool {
        if let labeled = item.as(LabeledStmtSyntax.self) {
            return isCode(Syntax(labeled.statement))
        }
        if let statement = item.as(ExpressionStmtSyntax.self) {
            return isCode(Syntax(statement.expression))
        }
        if let sequence = item.as(SequenceExprSyntax.self) {
            return !sequence.elements.allSatisfy(isHyphenatedWord)
        }
        return !notCode.contains(item.kind)
    }

    private static func isHyphenatedWord(_ element: ExprSyntax) -> Bool {
        element.is(DeclReferenceExprSyntax.self)
            || element.as(BinaryOperatorExprSyntax.self)?.operator.text == "-"
    }
}
