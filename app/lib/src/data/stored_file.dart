import 'dart:io';

/// Reading a stored record when "there is nothing here" and "there is something
/// here I cannot read" must not collapse into one answer.
///
/// Both stores in this directory hold a record whose absence means something
/// specific and whose damage means something else — for the I6 baseline those
/// two are accept-on-trust versus refuse, which is the difference between a
/// defence and its bypass. So absence is **proved** here rather than inferred.
///
/// The obvious inference is `File.exists()`, and it is wrong. Measured on this
/// platform, every one of these answers `false`:
///
/// | at the path                       | `readAsString` | `exists()` |
/// |-----------------------------------|----------------|------------|
/// | nothing                           | errno 2        | false      |
/// | a directory                       | errno 21       | false      |
/// | a dangling symlink                | **errno 2**    | false      |
/// | a real file, parent unsearchable  | errno 13       | false      |
///
/// Only the first is absence. Note the third: a dangling symlink raises the
/// *same* errno as a missing file, so classifying by errno gets it wrong too —
/// which is why the proof below is not "the error looks like not-found" but
/// **"the directory that would contain this name is readable, and the name is
/// not in it"**. A dangling symlink and a directory are both *in* the listing;
/// an unsearchable parent cannot be listed at all. Each of the four is a
/// different answer, and none of them is a guess.
///
/// What stays unproven, deliberately: if the containing directory is itself
/// missing, the record inside it cannot exist, so that is reported as absence —
/// and a *dangling symlink standing in for the directory* would be read the same
/// way. That is the one residue, it is one level up from the record this guards,
/// and the production path (`getApplicationDocumentsDirectory()`) is created by
/// the platform rather than by anything this app can point elsewhere.
sealed class StoredFile {
  const StoredFile();
}

/// The bytes, read whole.
final class StoredBytes extends StoredFile {
  const StoredBytes(this.bytes);

  final String bytes;
}

/// Proved absent: the container is readable and does not hold the name.
final class StoredAbsent extends StoredFile {
  const StoredAbsent();
}

/// Something is there, or the question could not be answered. Never absence.
final class StoredUnreadable extends StoredFile {
  const StoredUnreadable(this.reason);

  final String reason;
}

/// Read what [open] names, distinguishing absence from damage.
///
/// [open] is a callback rather than a path because both stores inject it, and
/// because locating the file is itself fallible: `path_provider` raises
/// `MissingPlatformDirectoryException`, which is not a [FileSystemException].
/// A failure to even name the file is not evidence that nothing is stored.
Future<StoredFile> readStoredFile(Future<File> Function() open) async {
  final File file;
  try {
    file = await open();
  } on Object catch (error) {
    return StoredUnreadable('the stored file could not be located: $error');
  }
  try {
    return StoredBytes(await file.readAsString());
  } on Object catch (error) {
    // Caught as `Object`, not `FileSystemException`: the set of types this can
    // raise is `dart:io`'s plus whatever the platform adds, and a type this
    // catch had not heard of would reach a caller that is holding a three-case
    // state and reasonably assuming the three cases are all of them.
    if (await _isAbsentFromItsDirectory(file)) {
      return const StoredAbsent();
    }
    return StoredUnreadable('the stored file could not be read: $error');
  }
}

/// The proof. False whenever it cannot be completed — an unanswerable question
/// is not a "no".
Future<bool> _isAbsentFromItsDirectory(File file) async {
  final name = _basename(file.path);
  try {
    await for (final entity in file.parent.list(followLinks: false)) {
      if (_basename(entity.path) == name) {
        return false;
      }
    }
    return true;
  } on PathNotFoundException {
    // The containing directory is not there either, so neither is the record.
    return true;
  } on Object {
    // Unsearchable, not a directory, or anything else: no proof, no absence.
    return false;
  }
}

String _basename(String path) => path.split(Platform.pathSeparator).last;
