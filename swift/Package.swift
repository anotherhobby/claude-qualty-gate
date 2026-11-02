// swift-tools-version:6.0
// The quality gate's Swift syntax helper: function spans for placement, cognitive
// complexity, and the rules SwiftLint does not hold. Built by the gate on first use.
import PackageDescription

let package = Package(
    name: "GateSyntax",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "gate-syntax", targets: ["gate-syntax"])],
    dependencies: [
        .package(url: "https://github.com/swiftlang/swift-syntax.git", exact: "603.0.2")
    ],
    targets: [
        .target(
            name: "GateSyntaxCore",
            dependencies: [
                .product(name: "SwiftSyntax", package: "swift-syntax"),
                .product(name: "SwiftParser", package: "swift-syntax"),
            ]
        ),
        .executableTarget(name: "gate-syntax", dependencies: ["GateSyntaxCore"]),
        .testTarget(name: "GateSyntaxCoreTests", dependencies: ["GateSyntaxCore"]),
    ]
)
