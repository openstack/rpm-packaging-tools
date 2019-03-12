#!/usr/bin/python
# Copyright (c) 2016 SUSE Linux GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import print_function

import argparse
from collections import namedtuple
import os
from packaging import version
from packaging.requirements import Requirement
import re
import requests
import sys
import yaml
import json

# the current 'in development' release
CURRENT_MASTER = 'stein'

# the host where to query for open reviews
GERRIT_HOST = 'https://review.openstack.org'

V = namedtuple('V', ['release', 'upper_constraints', 'rpm_packaging_pkg',
                     'reviews', 'obs_published'])


def process_args():
    parser = argparse.ArgumentParser(
        description='Compare rpm-packaging with OpenStack releases')
    parser.add_argument('releases-git-dir',
                        help='Base directory of the openstack/releases '
                        'git repo', default='releases')
    parser.add_argument('rpm-packaging-git-dir',
                        help='Base directory of the openstack/rpm-packaging '
                        'git repo', default='rpm-packaging')
    parser.add_argument('requirements-git-dir',
                        help='Base directory of the openstack/requirements '
                        'git repo', default='requirements')
    parser.add_argument('--obs-published-xml',
                        help='path to a published xml file from the '
                        'openbuildservice')
    parser.add_argument('release',
                        help='name of the release. I.e. "mitaka"',
                        default='mitaka')
    parser.add_argument('--include-projects', nargs='*',
                        metavar='project-name', default=[],
                        help='If non-empty, only the given '
                        'projects will be checked. default: %(default)s')
    parser.add_argument('--format',
                        help='output format', choices=('text', 'html'),
                        default='text')
    return vars(parser.parse_args())


def find_highest_release_version(releases):
    """get a list of dicts with a version key and find the highest version
    using PEP440 to compare the different versions"""
    return max(releases, key=lambda x: version.parse(str(x['version'])))


def _rpm_split_filename(filename):
    """Taken from yum's rpmUtils.miscutils.py file
    Pass in a standard style rpm fullname
    Return a name, version, release, epoch, arch, e.g.::
    foo-1.0-1.i386.rpm returns foo, 1.0, 1, i386
    1:bar-9-123a.ia64.rpm returns bar, 9, 123a, 1, ia64
    """
    if filename[-4:] == '.rpm':
        filename = filename[:-4]

    archIndex = filename.rfind('.')
    arch = filename[archIndex+1:]

    relIndex = filename[:archIndex].rfind('-')
    rel = filename[relIndex+1:archIndex]

    verIndex = filename[:relIndex].rfind('-')
    ver = filename[verIndex+1:relIndex]

    epochIndex = filename.find(':')
    if epochIndex == -1:
        epoch = ''
    else:
        epoch = filename[:epochIndex]

    name = filename[epochIndex + 1:verIndex]
    return name, ver, rel, epoch, arch


def find_openbuildservice_pkg_version(published_xml, pkg_name):
    """find the version in the openbuildservice published xml for the given
    pkg name"""
    import pymod2pkg
    import xml.etree.ElementTree as ET

    if published_xml and os.path.exists(published_xml):
        with open(published_xml) as f:
            tree = ET.fromstring(f.read())

        distro_pkg_name = pymod2pkg.module2package(pkg_name, 'suse')
        for child in tree:
            if not child.attrib['name'].startswith('_') and \
               child.attrib['name'].endswith('.rpm') and not \
               child.attrib['name'].endswith('.src.rpm'):
                (name, ver, release, epoch, arch) = _rpm_split_filename(
                    child.attrib['name'])
                if name == distro_pkg_name:
                    return version.parse(ver)
    return version.parse('0')


def find_rpm_packaging_pkg_version(pkg_project_spec):
    """get a spec.j2 template and get the version"""
    if os.path.exists(pkg_project_spec):
        with open(pkg_project_spec) as f:
            for l in f:
                # if the template variable 'upstream_version' is set, use that
                m = re.search(
                    "{%\s*set upstream_version\s*=\s*(?:upstream_version\()?"
                    "'(?P<version>.*)'(?:\))?\s*%}$", l)
                if m:
                    return version.parse(m.group('version'))
                # check the Version field
                m = re.search('^Version:\s*(?P<version>.*)\s*$', l)
                if m:
                    if m.group('version') == '{{ py2rpmversion() }}':
                        return 'version unset'
                    return version.parse(m.group('version'))
        # no version in spec found
        print('ERROR: no version in %s found' % pkg_project_spec)
        return version.parse('0')
    return version.parse('0')


def _pretty_table(release, projects, include_obs):
    from prettytable import PrettyTable
    tb = PrettyTable()
    fn = ['name',
          'release (%s)' % release,
          'u-c (%s)' % release,
          'rpm packaging (%s)' % release,
          'reviews']
    if include_obs:
        fn += ['obs']
    fn += ['comment']
    tb.field_names = fn

    for p_name, x in projects.items():
        if x.rpm_packaging_pkg == 'version unset':
            comment = 'ok'
        elif x.rpm_packaging_pkg == version.parse('0'):
            comment = 'unpackaged'
        elif x.rpm_packaging_pkg < x.release:
            comment = 'needs upgrade'
        elif x.rpm_packaging_pkg == x.release:
            if x.upper_constraints != '-' and \
                    x.release > version.parse(x.upper_constraints):
                comment = 'needs downgrade (u-c)'
            comment = 'ok'
        elif x.rpm_packaging_pkg > x.release:
            comment = 'needs downgrade'
        else:
            comment = ''
        row = [p_name, x.release, x.upper_constraints, x.rpm_packaging_pkg,
               x.reviews]
        if include_obs:
            row += [x.obs_published]
        row += [comment]

        tb.add_row(row)

    return tb


def output_text(release, projects, include_obs):
    tb = _pretty_table(release, projects, include_obs)
    print(tb.get_string(sortby='comment'))


def output_html(release, projects, include_obs):
    """adjust the comment color a big with an ugly hack"""
    from lxml import html
    tb = _pretty_table(release, projects, include_obs)
    s = tb.get_html_string(sortby='comment')
    tree = html.document_fromstring(s)
    tab = tree.cssselect('table')
    tab[0].attrib['style'] = 'border-collapse: collapse;'
    trs = tree.cssselect('tr')
    for t in trs:
        t.attrib['style'] = 'border-bottom:1pt solid black;'
    tds = tree.cssselect('td')
    for t in tds:
        if t.text_content() == 'unpackaged':
            t.attrib['style'] = 'background-color:yellow'
        elif t.text_content() == 'needs upgrade':
            t.attrib['style'] = 'background-color:LightYellow'
        elif t.text_content() == ('needs downgrade' or 'needs downgrade (uc)'):
            t.attrib['style'] = 'background-color:red'
        elif t.text_content() == 'ok':
            t.attrib['style'] = 'background-color:green'
    print(html.tostring(tree))


def read_upper_constraints(filename):
    uc = dict()
    with open(filename) as f:
        for l in f.readlines():
            # ignore markers for now
            l = l.split(';')[0]
            r = Requirement(l)
            for s in r.specifier:
                uc[r.name] = s.version
                # there is only a single version in upper constraints
                break
    return uc


def _gerrit_open_reviews_per_file(release):
    """Returns a dict with filename as key and a list of review numbers
    where this file is modified as value"""
    # NOTE: gerrit has a strange first line in the returned data
    gerrit_strip = ')]}\'\n'
    data = dict()

    if release == CURRENT_MASTER:
        branch = 'master'
    else:
        branch = 'stable/%s' % release

    url_reviews = GERRIT_HOST + '/changes/?q=status:open+project:openstack/' \
                                'rpm-packaging+branch:%s' % branch
    res_reviews = requests.get(url_reviews)
    if res_reviews.status_code == 200:
        data_reviews = json.loads(res_reviews.text.lstrip(gerrit_strip))
        for review in data_reviews:
            url_files = GERRIT_HOST + '/changes/%s/revisions/current/files/' \
                        % review['change_id']
            res_files = requests.get(url_files)
            if res_files.status_code == 200:
                data_files = json.loads(res_files.text.lstrip(gerrit_strip))
                for f in data_files.keys():
                    # extract project name
                    if f.startswith('openstack/') and f.endswith('spec.j2'):
                        f = f.split('/')[1]
                        data.setdefault(f, []).append(review['_number'])
    return data


def main():
    args = process_args()

    projects = {}

    upper_constraints = read_upper_constraints(
        os.path.join(args['requirements-git-dir'], 'upper-constraints.txt'))

    # open reviews for the given release
    open_reviews = _gerrit_open_reviews_per_file(args['release'])

    # directory which contains all yaml files from the openstack/release
    # git dir
    releases_yaml_dir = os.path.join(args['releases-git-dir'], 'deliverables',
                                     args['release'])
    releases_indep_yaml_dir = os.path.join(args['releases-git-dir'],
                                           'deliverables', '_independent')
    yaml_files = [os.path.join(releases_indep_yaml_dir, f)
                  for f in os.listdir(releases_indep_yaml_dir)]
    yaml_files += [os.path.join(releases_yaml_dir, f)
                   for f in os.listdir(releases_yaml_dir)]
    for yaml_file in yaml_files:
        project_name = re.sub('\.ya?ml$', '', os.path.basename(yaml_file))
        # skip projects if include list is given
        if len(args['include_projects']) and \
           project_name not in args['include_projects']:
            continue
        with open(yaml_file) as f:
            data = yaml.load(f.read())
            if 'releases' not in data:
                # there might be yaml files without any releases
                continue
            v_release = find_highest_release_version(data['releases'])
        # use tarball-base name if available
        project_name_pkg = v_release['projects'][0].get('tarball-base',
                                                        project_name)

        # get version from upper-constraints.txt
        if project_name in upper_constraints:
            v_upper_constraints = upper_constraints[project_name]
        else:
            v_upper_constraints = '-'

        # path to the corresponding .spec.j2 file
        rpm_packaging_pkg_project_spec = os.path.join(
            args['rpm-packaging-git-dir'],
            'openstack', project_name_pkg,
            '%s.spec.j2' % project_name_pkg)
        v_rpm_packaging_pkg = find_rpm_packaging_pkg_version(
            rpm_packaging_pkg_project_spec)

        # version from build service published file
        v_obs_published = find_openbuildservice_pkg_version(
            args['obs_published_xml'], project_name)

        # reviews for the given project
        if project_name in open_reviews:
            project_reviews = open_reviews[project_name]
        else:
            project_reviews = []

        # add both versions to the project dict
        projects[project_name] = V(version.parse(v_release['version']),
                                   v_upper_constraints,
                                   v_rpm_packaging_pkg,
                                   project_reviews,
                                   v_obs_published)

    include_obs = args['obs_published_xml']
    if args['format'] == 'text':
        output_text(args['release'], projects, include_obs)
    elif args['format'] == 'html':
        output_html(args['release'], projects, include_obs)

    return 0


if __name__ == '__main__':
    sys.exit(main())
