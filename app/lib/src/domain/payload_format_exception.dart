/// The payload body is not a shape this build understands.
///
/// Raised rather than defaulted. A field we cannot read is not a field we may
/// guess at: the total would still render, and it would render a number whose
/// age we had invented.
class PayloadFormatException implements Exception {
  const PayloadFormatException(this.message);

  final String message;

  @override
  String toString() => 'PayloadFormatException: $message';
}
