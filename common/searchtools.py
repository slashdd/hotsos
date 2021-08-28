# Copyright 2021 Edward Hope-Morley
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import os
import sys

import glob
import gzip
import multiprocessing
import re
import uuid

from common import constants


class FileSearchException(Exception):
    def __init__(self, msg):
        self.msg = msg


class FilterDef(object):

    def __init__(self, pattern, invert_match=False):
        """
        Add a filter definition

        @param pattern: regex pattern to search for
        @param invert_match: return True if match is positive
        """
        self.pattern = re.compile(pattern)
        self.invert_match = invert_match

    def filter(self, line):
        ret = self.pattern.search(line)
        if self.invert_match:
            return ret is not None
        else:
            return ret is None


class SearchDef(object):

    def __init__(self, pattern, tag=None, hint=None):
        """
        Add a search definition

        @param pattern: regex pattern or list of patterns to search for
        @param tag: optional user-friendly identifier for this search term
        @param hint: pre-search term to speed things up
        """
        if type(pattern) != list:
            self.patterns = [re.compile(pattern)]
        else:
            self.patterns = []
            for _pattern in pattern:
                self.patterns.append(re.compile(_pattern))

        self.tag = tag
        if hint:
            self.hint = re.compile(hint)
        else:
            self.hint = None

    def run(self, line):
        """Execute search patterns against line and return first match."""
        if self.hint:
            ret = self.hint.search(line)
            if not ret:
                return None

        ret = None
        for pattern in self.patterns:
            ret = pattern.match(line)
            if ret:
                return ret

        return ret


class SequenceSearchDef(object):

    def __init__(self, start, tag, end=None, body=None):
        """
        Information required to define and perfom searches for sequences of
        data whereby a sequence must match a start and end with optional
        body in between. The end of a sequence can be defined by end or of not
        provided, can be the start of a new section of EOF.

        NOTE: sequences must not be overlapping. This is therefore not
        suitable for finding sequences generated by parallel tasks in a single
        file where those sequences may be overlapping.

        @param start: SearchDef object for matching start
        @param tag: tag used to identify this sequence definition
        @param end: optional SearchDef object for matching end
        @param body: optional SearchDef object for matching body
        """
        self.s_start = start
        self.s_end = end
        self.s_body = body
        self.tag = tag
        self._mark = None
        self._section_idx = 0
        self._unique_id = str(uuid.uuid4())

    @property
    def start_tag(self):
        """Tag used to identify start of section"""
        return "{}-start".format(self.tag)

    @property
    def end_tag(self):
        """Tag used to identify end of section"""
        return "{}-end".format(self.tag)

    @property
    def body_tag(self):
        """Tag used to identify body of section"""
        return "{}-body".format(self.tag)

    @property
    def id(self):
        return self._unique_id

    @property
    def section_idx(self):
        """
        Index of current section. Starts from 0 and gets incremented after
        every completed section.
        """
        return self._section_idx

    @property
    def started(self):
        """Indicate a section sequence has been started."""
        return self._mark == 1

    def start(self):
        """Indicate that a sequence start has been detected."""
        self._mark = 1

    def reset(self):
        """Used to restart a section. This is used e.g. if the start
        expression matches midway through a sequence (and before the end).
        """
        self._mark = 0

    def stop(self):
        """Indicate that a sequence is complete."""
        self._mark = 0
        self._section_idx += 1


class SearchResultPart(object):

    def __init__(self, index, value):
        self.index = index
        self.value = value


class SearchResult(object):

    def __init__(self, linenumber, source, result, search_term_tag=None,
                 section_idx=None, sequence_obj_id=None):
        """
        @param linenumber: line number that produced a match
        @param source: data source (path)
        @param result: python.re match object
        @param search_term_tag: SearchDef object tag
        @param section_idx: SequenceSearchDef object section id
        @param sequence_obj_id: SequenceSearchDef object unique id
        """
        self.tag = search_term_tag
        self.source = source
        self.linenumber = linenumber
        self._parts = {}
        self.sequence_obj_id = sequence_obj_id
        self.section_idx = section_idx
        num_groups = len(result.groups())
        # NOTE: this does not include group(0)
        if num_groups:
            # To reduce memory footprint, don't store group(0) i.e. the whole
            # line, if there are actual groups in the result.
            for i in range(1, num_groups + 1):
                self._add(i, result.group(i))
        else:
            self._add(0, result.group(0))

    def _add(self, index, value):
        self._parts[index] = SearchResultPart(index, value)

    def get(self, index):
        """Retrieve a result part by its index."""
        if index not in self._parts:
            return None

        return self._parts[index].value


class SearchResultsCollection(object):

    def __init__(self):
        self.reset()

    @property
    def files(self):
        return list(self._results.keys())

    def reset(self):
        self._iter_idx = 0
        self._results = {}

    def add(self, path, results):
        if path not in self._results:
            self._results[path] = results
        else:
            self._results[path] += results

    def find_by_path(self, path):
        if path not in self._results:
            return []

        return self._results[path]

    def find_by_tag(self, tag, path=None, sequence_obj_id=None):
        """Return all result tagged with tag.

        If no path is provided tagged results from all paths are returned.
        """
        if path:
            paths = [path]
        else:
            paths = list(self._results.keys())

        results = []
        for path in paths:
            for result in self._results.get(path, []):
                if sequence_obj_id is None:
                    if result.tag == tag:
                        results.append(result)
                else:
                    if (result.tag == tag and
                            result.sequence_obj_id == sequence_obj_id):
                        results.append(result)

        return results

    def find_sequence_sections(self, sequence_obj, path=None):
        """Return results of running the given sequence search.

        Returns a dictionary keyed by section id where each is a list of
        results for that section with start, body, end etc.
        """
        _results = []
        sections = {}
        _results += self.find_by_tag(tag=sequence_obj.start_tag, path=path,
                                     sequence_obj_id=sequence_obj.id)
        _results += self.find_by_tag(tag=sequence_obj.body_tag, path=path,
                                     sequence_obj_id=sequence_obj.id)
        _results += self.find_by_tag(tag=sequence_obj.end_tag, path=path,
                                     sequence_obj_id=sequence_obj.id)
        for r in _results:
            if r.section_idx in sections:
                sections[r.section_idx].append(r)
            else:
                sections[r.section_idx] = [r]

        return sections

    def __iter__(self):
        return iter(self._results.items())


class FileSearcher(object):

    def __init__(self):
        self.paths = {}
        self.filters = {}
        self.results = SearchResultsCollection()

    @property
    def num_cpus(self):
        if constants.MAX_PARALLEL_TASKS == 0:
            cpus = 1  # i.e. no parallelism
        else:
            cpus = min(constants.MAX_PARALLEL_TASKS, os.cpu_count())

        return cpus

    def add_filter_term(self, filter, path):
        """Add a term to search for that will be used as a filter for the given
        data source. This filter is applied to each line in a file prior to
        executing the full search(es) as means of reducing the amount of full
        searches we have to do by filtering out lines that do not qualify. A
        negative match results in the line being skipped and no further
        searches performed.

        A filter definition is registered against a path which can be a
        file,  directory or glob. Any number of filters can be registered.

        @param filtedef: FilterDef object
        @param path: path to which the filter should be applied.
        """
        if path in self.filters:
            self.filters[path].append(filter)
        else:
            self.filters[path] = [filter]

    def add_search_term(self, searchdef, path):
        """Add a term to search for.

        A search definition is registered against a path which can be a
        file,  directory or glob. Any number of searches can be registered.
        Searches are executed concurrently by file.

        @param searchdef: SearchDef object
        @param path: path that we will be searching for this key
        """
        if path in self.paths:
            self.paths[path].append(searchdef)
        else:
            self.paths[path] = [searchdef]

    def _job_wrapper(self, pool, path, entry):
        term_key = path
        return pool.apply_async(self._search_task_wrapper,
                                (entry, term_key))

    def _search_task_wrapper(self, path, term_key):
        try:
            with gzip.open(path, 'r') as fd:
                try:
                    # test if file is gzip
                    fd.read(1)
                    fd.seek(0)
                    return self._search_task(term_key, fd, path)
                except OSError:
                    pass

            with open(path) as fd:
                return self._search_task(term_key, fd, path)
        except EOFError as e:
            msg = ("an exception occured while searching {} - {}".
                   format(path, e))
            raise FileSearchException(msg) from e
        except Exception as e:
            msg = ("an unknown exception occured while searching {} - {}".
                   format(path, e))
            raise FileSearchException(msg) from e

    def line_filtered(self, term_key, line):
        """Returns True if line is to be skipped."""
        for f_term in self.filters.get(term_key, []):
            if f_term.filter(line):
                return True

        return False

    def _search_task(self, term_key, fd, path):
        results = []
        sequence_results = {}
        for ln, line in enumerate(fd, start=1):
            if type(line) == bytes:
                line = line.decode("utf-8")

            # global filters (untagged)
            if self.line_filtered(term_key, line):
                continue

            for s_term in self.paths[term_key]:
                if type(s_term) == SequenceSearchDef:
                    # if the ending is defined and we match a start while
                    # already in a section, we start again.
                    if s_term.s_end:
                        ret = s_term.s_start.run(line)
                        if s_term.started:
                            if ret:
                                # reset and start again
                                if sequence_results:
                                    del sequence_results[s_term.id]

                                s_term.reset()
                            else:
                                ret = s_term.s_end.run(line)
                    else:
                        ret = s_term.s_start.run(line)
                else:
                    ret = s_term.run(line)

                if ret:
                    section_idx = None
                    sequence_obj_id = None
                    tag = s_term.tag
                    if type(s_term) == SequenceSearchDef:
                        if not s_term.started:
                            tag = s_term.start_tag
                            s_term.start()
                            section_idx = s_term.section_idx
                        else:
                            tag = s_term.end_tag
                            section_idx = s_term.section_idx
                            s_term.stop()
                            # if no end is defined then we dont bother storing
                            # the result, just complete the section and start
                            # the next.
                            if s_term.s_end is None:
                                tag = s_term.start_tag
                                s_term.start()
                                section_idx = s_term.section_idx

                        sequence_obj_id = s_term.id

                    r = SearchResult(ln, path, ret, tag,
                                     section_idx=section_idx,
                                     sequence_obj_id=sequence_obj_id)
                    if type(s_term) == SequenceSearchDef:
                        if s_term.id not in sequence_results:
                            sequence_results[s_term.id] = [r]
                        else:
                            sequence_results[s_term.id].append(r)
                    else:
                        results.append(r)

                elif type(s_term) == SequenceSearchDef:
                    if s_term.started and s_term.s_body:
                        ret = s_term.s_body.run(line)
                        if not ret:
                            continue

                        r = SearchResult(ln, path, ret, s_term.body_tag,
                                         section_idx=s_term.section_idx,
                                         sequence_obj_id=s_term.id)
                        sequence_results[s_term.id].append(r)

        if sequence_results:
            # If a sequence ending definition is provided and we reached EOF
            # while a sequence is started, complete the sequence is s_end
            # matches an empty string. If none is defined we just go ahead and
            # complete the section.
            filter_section_idx = []
            for s_term in self.paths[term_key]:
                if type(s_term) == SequenceSearchDef:
                    if s_term.started:
                        if s_term.s_end is None:
                            s_term.stop()
                        else:
                            ret = s_term.s_end.run("")
                            if ret:
                                section_idx = s_term.section_idx
                                s_term.stop()
                                tag = s_term.end_tag
                                r = SearchResult(ln + 1, path, ret, tag,
                                                 section_idx=section_idx,
                                                 sequence_obj_id=s_term.id)
                            else:
                                filter_section_idx.append(s_term.section_idx)

            # Now add sequece results to main results list, excluding any
            # incomplete sections.
            for s_results in sequence_results.values():
                for r in s_results:
                    if filter_section_idx:
                        if r.section_idx in filter_section_idx:
                            continue

                    results.append(r)

        return results

    def logrotate_file_sort(self, fname):
        """
        This is used to sort the contents of a directory by passing the
        function as a the key to a list sort.

        Assumes that the filenames use logrotate format i.e. .log, .log.1,
        .log.2.gz etc.
        """

        filters = [r"\S+\.log$",
                   r"\S+\.log\.(\d+)$",
                   r"\S+\.log\.(\d+)\.gz?$"]
        for filter in filters:
            ret = re.compile(filter).match(fname)
            if ret:
                break

        # files that dont follow logrotate naming format go to the end.
        if not ret:
            # put at the end
            return 100000

        if len(ret.groups()) == 0:
            return 0

        return int(ret.group(1))

    def filtered_paths(self, paths):
        """
        Paths can be a mix of files and directories.
        """
        logrotate_collection = {}
        dir_contents = []
        for path in paths:
            if not os.path.isfile(path):
                continue

            ret = re.compile(r"(\S+)\.log\S*").match(path)
            if ret:
                base = ret.group(1)
                if base not in logrotate_collection:
                    logrotate_collection[base] = []

                if re.compile(r"(\S+)\.log\S+").match(path):
                    logrotate_collection[base].append(path)
                else:
                    dir_contents.append(base + '.log')
            else:
                dir_contents.append(path)

        limit = constants.MAX_LOGROTATE_DEPTH
        for logrotated in logrotate_collection.values():
            capped = sorted(logrotated,
                            key=self.logrotate_file_sort)[:limit]
            dir_contents += capped

        return dir_contents

    def search(self):
        """Execute all the search queries.

        @return: search results
        """
        self.results.reset()
        with multiprocessing.Pool(processes=self.num_cpus) as pool:
            jobs = {}
            for user_path in self.paths:
                jobs[user_path] = []
                if os.path.isfile(user_path):
                    job = self._job_wrapper(pool, user_path, user_path)
                    jobs[user_path] = [(user_path, job)]
                elif os.path.isdir(user_path):
                    for path in self.filtered_paths(user_path):
                        job = self._job_wrapper(pool, user_path, path)
                        jobs[user_path].append((path, job))
                else:
                    for path in self.filtered_paths(glob.glob(user_path)):
                        job = self._job_wrapper(pool, user_path, path)
                        jobs[user_path].append((path, job))

            for user_path in jobs:
                for fpath, job in jobs[user_path]:
                    try:
                        result = job.get()
                        self.results.add(fpath, result)
                    except FileSearchException as e:
                        sys.stderr.write("{}\n".format(e.msg))

        return self.results
