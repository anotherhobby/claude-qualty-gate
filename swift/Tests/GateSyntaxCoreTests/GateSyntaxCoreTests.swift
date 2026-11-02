import Testing

@testable import GateSyntaxCore

private let noLimits = Limits(cognitive: .max, viewBody: .max)

private func rules(_ text: String, limits: Limits = noLimits) -> [String] {
    check(text, limits: limits).findings.map(\.rule)
}

/// The cognitive complexity `check` reports for the only function in `text`, or 0.
private func cognitive(_ text: String) -> Int {
    let finding = check(text, limits: Limits(cognitive: 0, viewBody: .max)).findings.first {
        $0.rule == "cognitive-complexity"
    }
    let digits = finding?.message.split(separator: " ").dropLast(2).last
    return digits.flatMap { Int($0) } ?? 0
}

@Suite struct SpanNames {
    @Test func namesThroughTypesAndExtensionsWithLabels() {
        let text = """
            struct Panel {
                init(id: Int) {}
                func move(to point: Int, _ by: Int) {}
                var area: Int { 1 }
                let fixed = 2
            }
            extension Panel {
                subscript(index: Int) -> Int { index }
            }
            """
        let names = spans(of: text).map(\.name)
        #expect(
            names == [
                "Panel", "Panel.init(id:)", "Panel.move(to:_:)", "Panel.area", "Panel",
                "Panel.subscript(index:)",
            ])
    }

    @Test func closuresAreNumberedWithinTheirFunction() {
        let text = """
            func run() {
                let first = { 1 }
                let second = { { 2 } }
            }
            let handler = { 3 }
            """
        let names = spans(of: text).map(\.name)
        #expect(
            names == [
                "run()", "run().first.{closure#1}", "run().second.{closure#1}",
                "run().second.{closure#1}.{closure#1}", "handler.{closure#1}",
            ])
    }

    @Test func spanStartsAtItsAttributesNotItsComments() {
        let text = """
            // A comment.
            @MainActor
            func run() {
            }
            """
        #expect(spans(of: text) == [Span(name: "run()", first: 2, last: 4)])
    }
}

@Suite struct CognitiveComplexity {
    @Test func nestingAddsToEachStructure() {
        let text = """
            func f(a: Bool, b: [Int]) {
                if a {                 // +1
                    for _ in b {       // +2
                        while a {}     // +3
                    }
                }
            }
            """
        #expect(cognitive(text) == 6)
    }

    @Test func elseIfAndElseAddOneEach() {
        let text = """
            func f(a: Int) {
                if a == 1 {} else if a == 2 {} else {}
            }
            """
        #expect(cognitive(text) == 3)
    }

    @Test func runsOfLogicalOperators() {
        #expect(cognitive("func f(a: Bool) { if a && a && a {} }") == 2)
        #expect(cognitive("func f(a: Bool) { if a && a || a {} }") == 3)
    }

    @Test func closuresAreMeasuredApart() {
        let text = """
            func f(a: Bool) {
                let g = {
                    if a {}
                }
            }
            """
        let report = check(text, limits: Limits(cognitive: 0, viewBody: .max))
        #expect(report.findings.map(\.line) == [2])
    }

    @Test func switchCountsOnceAndTernaryNests() {
        let text = """
            func f(a: Int) -> Int {
                switch a {
                case 1: return a > 0 ? 1 : 2
                default: return 0
                }
            }
            """
        #expect(cognitive(text) == 3)
    }

    @Test func guardAndCatch() {
        let text = """
            func f(a: Int?) {
                guard let a else { return }
                do { try g() } catch { }
            }
            """
        #expect(cognitive(text) == 2)
    }
}

@Suite struct Rules {
    @Test func everyTryOptional() {
        #expect(rules("func f() { _ = try? g() }") == ["try-optional"])
        #expect(rules("func f() { if let x = try? g() {} }") == ["try-optional"])
        #expect(rules("func f() throws { try g(); _ = try! g() }").isEmpty)
    }

    @Test func uncheckedSendableAndNonisolatedUnsafe() {
        #expect(rules("final class C: @unchecked Sendable {}") == ["unchecked-sendable"])
        #expect(rules("nonisolated(unsafe) var x = 0") == ["nonisolated-unsafe"])
        #expect(rules("nonisolated func f() {}").isEmpty)
    }

    @Test func viewBodyCountsLinesOfCode() {
        let body = (1...4).map { "        Text(\"\($0)\")" }.joined(separator: "\n")
        let text =
            "struct V: View {\n    var body: some View {\n\(body)\n\n        // note\n    }\n}"
        let limits = Limits(cognitive: .max, viewBody: 3)
        #expect(rules(text, limits: limits) == ["view-body-length"])
        #expect(rules(text, limits: Limits(cognitive: .max, viewBody: 4)).isEmpty)
    }
}

@Suite struct CommentRules {
    @Test func swiftlintDisableIsAlwaysAFinding() {
        #expect(rules("// swiftlint:disable force_cast\nlet x = 1") == ["swiftlint-disable"])
        #expect(rules("let x = 1 // swiftlint:disable:this force_cast") == ["swiftlint-disable"])
    }

    @Test func commentedOutCode() {
        #expect(rules("// let x = compute(1)\n// print(x)\nlet y = 2") == ["commented-out-code"])
        #expect(rules("func f() {\n    // return g()\n}") == ["commented-out-code"])
    }

    @Test func proseIsNotCode() {
        let prose = [
            "// Returns the value.", "// TODO: fix", "// MARK: - Section", "// Note",
            "// https://example.com/a", "// e.g. the panel", "/// let x = 1",
            "// swift-format-ignore", "let x = 1 // set(x)",
        ]
        for comment in prose {
            #expect(rules(comment + "\nlet y = 2").isEmpty, "\(comment)")
        }
    }
}
