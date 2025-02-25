import sys
from sre_parse import SubPattern, parse
from sre_compile import compile as sre_compile
from sre_constants import BRANCH, SUBPATTERN

try:
    basestring
except NameError:
    basestring = str

try:
    # Pattern will be renamed to State in 3.8?
    # https://github.com/python/cpython/commit/e0c19ddc661e56cc7e694be52d3e47f1dfe5af24#diff-daea55f2fca784d9a64ca8f190403904
    from sre_parse import State as Pattern
except ImportError:
    from sre_parse import Pattern

try:
    from re import RegexFlag
except ImportError:
    # RegexFlags is not public, but it is an Enum; so use that instead if
    # RegexFlags is not available
    # (We only need it for an instanceof check)
    from enum import Enum as RegexFlag


# https://github.com/python/cpython/commit/be9a4e5c855188cf146962483e6de942bf154d95
append_subpattern_extra_flags = (0, 0) if sys.version_info >= (3, 6) else ()


class _ScanMatch(object):

    def __init__(self, match, rule, start, end):
        self._match = match
        self._start = start
        self._end = end
        self._rule = rule

    def __getattr__(self, name):
        return getattr(self._match, name)

    def __group_proc(self, method, group):
        if group == 0:
            return method()
        if isinstance(group, basestring):
            return method('\x00'.join(self._rule, group))
        real_group = self._start + group
        if real_group > self._end:
            raise IndexError('no such group')
        return method(real_group)

    def group(self, *groups):
        if len(groups) in (0, 1):
            return self.__group_proc(self._match.group,
                                  groups and groups[0] or 0)
        return tuple(self.__group_proc(self._match.group, group)
                     for group in groups)

    def groupdict(self, default=None):
        prefix = self._rule + '\x00'
        rv = {}
        for key, value in self._match.groupdict(default).iteritems():
            if key.startswith(prefix):
                rv[key[len(prefix):]] = value
        return rv

    def span(self, group=0):
        return self.__group_proc(self._match.span, group)

    def groups(self):
        return self._match.groups()[self._start:self._end]

    def start(self, group=0):
        return self.__group_proc(self._match.start, group)

    def end(self, group=0):
        return self.__group_proc(self._match.end, group)

    def expand(self, template):
        raise RuntimeError('Unsupported on scan matches')


class ScanEnd(Exception):

    def __init__(self, pos):
        Exception.__init__(self, pos)
        self.pos = pos


class Scanner(object):

    def __init__(self, rules, flags=0):
        # Convert the flags to an int if they are not already
        # https://github.com/python/cpython/commit/c1c47c166b1012d34f2c6e111ee9ccb5c4d12de7
        if isinstance(flags, RegexFlag):
            flags = flags.value

        pattern = Pattern()
        pattern.flags = flags
        pattern.groups = len(rules) + 1

        _og = pattern.opengroup
        pattern.opengroup = lambda n: _og('\x00'.join((name, n)) if n else n)

        self.rules = []
        subpatterns = []
        for group, (name, regex) in enumerate(rules, 1):
            last_group = pattern.groups - 1
            subpatterns.append(SubPattern(pattern, [
                (SUBPATTERN, (group, *append_subpattern_extra_flags,
                    parse(regex, flags, pattern))),
            ]))
            self.rules.append((name, last_group, pattern.groups - 1))

        self._scanner = sre_compile(SubPattern(
            pattern, [(BRANCH, (None, subpatterns))])).scanner

    def scan(self, string, skip=False):
        sc = self._scanner(string)

        match = None
        for match in iter(sc.search if skip else sc.match, None):
            rule, start, end = self.rules[match.lastindex - 1]
            yield rule, _ScanMatch(match, rule, start, end)

        if not skip:
            end = match and match.end() or 0
            if end < len(string):
                raise ScanEnd(end)

    def scan_with_holes(self, string):
        pos = 0
        for rule, match in self.scan(string, skip=True):
            hole = string[pos:match.start()]
            if hole:
                yield None, hole
            yield rule, match
            pos = match.end()
        hole = string[pos:]
        if hole:
            yield None, hole
