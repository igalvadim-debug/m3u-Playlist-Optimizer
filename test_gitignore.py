"""Regression tests for Git's handling of the repository ignore rules."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('git'), 'Git is required to test ignore rules')
class GitIgnoreTests(unittest.TestCase):
    def setUp(self):
        """Create a throwaway Git repository seeded with the project's .gitignore."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        # Ignore caller Git settings, including any alternate work tree or index.
        self.git_env = {key: value for key, value in os.environ.items()
                        if not key.startswith('GIT_')}
        self.git_env.update(GIT_CONFIG_NOSYSTEM='1',
                            GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_TERMINAL_PROMPT='0')
        self._git('init', '--quiet', '--template=')
        shutil.copyfile(Path(__file__).resolve().with_name('.gitignore'),
                        self.repo / '.gitignore')

    def _git(self, *args):
        """Run a Git command inside the sandboxed repository and return its stdout."""
        return subprocess.run(
            ['git', '-c', f'core.excludesFile={os.devnull}', *args],
            cwd=self.repo, env=self.git_env, check=True,
            capture_output=True, text=True, timeout=5).stdout

    def _write_files(self, paths):
        """Create each of the given relative paths as a synthetic fixture file."""
        for relative in paths:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic test fixture\n', encoding='utf-8')

    def _untracked_files(self):
        """Return the set of paths Git reports as untracked under the exclude rules."""
        # Ask Git which files would appear as untracked, without staging them.
        return set(self._git('ls-files', '--others', '--exclude-standard', '-z')
                   .rstrip('\0').split('\0'))

    def test_python_cache_directories_are_ignored_at_any_depth(self):
        """__pycache__ directories should be ignored regardless of nesting depth."""
        self._write_files([
            '__pycache__/app.cpython-312.pyc',
            'modules/__pycache__/organizer.cpython-312.pyc',
            'nested/package/__pycache__/module.cpython-314.pyc',
        ])
        self.assertEqual(self._untracked_files(), {'.gitignore'})

    def test_cache_directories_ignore_all_contents_and_descendants(self):
        """All files and subdirectories inside a __pycache__ directory stay ignored."""
        self._write_files([
            '__pycache__/metadata.json',
            '__pycache__/.env',
            '__pycache__/nested/playlist.m3u',
            'modules/__pycache__/nested/deeper/notes.txt',
        ])
        self.assertEqual(self._untracked_files(), {'.gitignore'})

    def test_standalone_bytecode_files_are_visible(self):
        """Loose .pyc files outside a cache directory should remain tracked as visible."""
        paths = {'app.pyc', 'modules/organizer.pyc',
                 'nested/package/module.cpython-312.pyc'}
        self._write_files(paths)
        self.assertEqual(self._untracked_files(), paths | {'.gitignore'})

    def test_virtual_environment_contents_are_visible(self):
        """Files inside a venv directory should not be excluded by .gitignore."""
        paths = {'venv/pyvenv.cfg', 'venv/bin/python',
                 'venv/Lib/site-packages/package.py'}
        self._write_files(paths)
        self.assertEqual(self._untracked_files(), paths | {'.gitignore'})

    def test_environment_files_are_visible_at_any_depth(self):
        """.env files at any nesting depth should remain visible to Git."""
        paths = {'.env', 'modules/.env', 'nested/package/.env'}
        self._write_files(paths)
        self.assertEqual(self._untracked_files(), paths | {'.gitignore'})

    def test_modules_archives_and_generated_outputs_are_visible(self):
        """Archived modules and generated outputs should remain untracked but visible."""
        paths = {'modules.zip', 'nested/modules.zip',
                 'outputs/cleaned.m3u', 'outputs/2026-10-17/playlist.pdf',
                 'outputs/folder with spaces/playlist.md'}
        self._write_files(paths)
        self.assertEqual(self._untracked_files(), paths | {'.gitignore'})

    def test_caches_stay_ignored_inside_newly_visible_directories(self):
        """__pycache__ directories remain ignored even inside otherwise visible directories."""
        visible = {'venv/pyvenv.cfg', 'outputs/playlist.m3u', 'modules/.env'}
        self._write_files(visible | {
            'venv/Lib/__pycache__/package.pyc',
            'outputs/__pycache__/generated.pyc',
            'modules/__pycache__/module.pyc',
        })
        self.assertEqual(self._untracked_files(), visible | {'.gitignore'})

    def test_cache_rule_matches_directories_with_the_exact_name_only(self):
        """Only directories named exactly __pycache__ should match the ignore rule."""
        paths = {'__pycache__', 'modules/__pycache__',
                 '__pycache__backup/module.pyc',
                 'nested/prefix__pycache__/module.pyc',
                 'nested/__pycache__.pyc'}
        self._write_files(paths)
        self.assertEqual(self._untracked_files(), paths | {'.gitignore'})


if __name__ == '__main__':
    unittest.main()
