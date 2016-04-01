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
import re
import sys
import yaml

# do some project name corrections if needed
projects_mapping = {
    "keystoneauth": "keystoneauth1"
}


V = namedtuple('V', ['release', 'rpm_packaging_pkg'])


def process_args():
    parser = argparse.ArgumentParser(
        description='Compare rpm-packaging with OpenStack releases')
    parser.add_argument('releases-git-dir',
                        help='Base directory of the openstack/releases '
                        'git repo', default='releases')
    parser.add_argument('rpm-packaging-git-dir',
                        help='Base directory of the openstack/rpm-packaging '
                        'git repo', default='rpm-packaging')
    parser.add_argument('release',
                        help='name of the release. I.e. "liberty"',
                        default='liberty')
    parser.add_argument('--include-projects', nargs='*', metavar='project-name',
                        default=[], help='If non-empty, only the given '
                        'projects will be checked. default: %(default)s')
    parser.add_argument('--format',
                        help='output format', choices=('text', 'html'),
                        default='text')
    return vars(parser.parse_args())


def find_highest_release_version(releases):
    """get a list of dicts with a version key and find the highest version
    using PEP440 to compare the different versions"""
    return max(version.parse(r['version']) for r in releases)


def find_rpm_packaging_pkg_version(pkg_project_spec):
    """get a spec.j2 template and get the version"""
    if os.path.exists(pkg_project_spec):
        with open(pkg_project_spec) as f:
            for l in f:
                m = re.search('^Version:\s*(?P<version>.*)\s*$', l)
                if m:
                    return version.parse(m.group('version'))
        # no version in spec found
        print('ERROR: no version in %s found' % pkg_project_spec)
        return version.parse('0')
    return version.parse('0')


def _pretty_table(release, projects):
    from prettytable import PrettyTable
    tb = PrettyTable()
    tb.field_names = ['name', 'release (%s)' % release,
                      'rpm packaging (%s)' % release, 'comment']
    for p_name, x in projects.items():
        if x.rpm_packaging_pkg == version.parse('0'):
            comment = 'needs packaging'
        elif x.rpm_packaging_pkg < x.release:
            comment = 'needs upgrade'
        elif x.rpm_packaging_pkg == x.release:
            comment = 'perfect'
        elif x.rpm_packaging_pkg > x.release:
            comment = 'needs downgrade'
        else:
            comment = ''
        tb.add_row([p_name, x.release, x.rpm_packaging_pkg, comment])
    return tb


def output_text(release, projects):
    tb = _pretty_table(release, projects)
    print(tb.get_string(sortby='name'))


def output_html(release, projects):
    """adjust the comment color a big with an ugly hack"""
    from lxml import html
    tb = _pretty_table(release, projects)
    s = tb.get_html_string(sortby='name')
    tree = html.document_fromstring(s)
    tab = tree.cssselect('table')
    tab[0].attrib['style'] = 'border-collapse: collapse;'
    trs = tree.cssselect('tr')
    for t in trs:
        t.attrib['style'] = 'border-bottom:1pt solid black;'
    tds = tree.cssselect('td')
    for t in tds:
        if t.text_content() == 'needs packaging':
            t.attrib['style'] = 'background-color:yellow'
        elif t.text_content() == 'needs upgrade':
            t.attrib['style'] = 'background-color:LightYellow'
        elif t.text_content() == 'needs downgrade':
            t.attrib['style'] = 'background-color:red'
        elif t.text_content() == 'perfect':
            t.attrib['style'] = 'background-color:green'
    print(html.tostring(tree))


def main():
    args = process_args()

    projects = {}

    # directory which contains all yaml files from the openstack/release git dir
    releases_yaml_dir = os.path.join(args['releases-git-dir'], 'deliverables',
                            args['release'])
    for yaml_file in os.listdir(releases_yaml_dir):
        project_name = re.sub('.yaml$', '', yaml_file)
        # skip projects if include list is given
        if len(args['include_projects']) and \
           project_name not in args['include_projects']:
            continue
        with open(os.path.join(releases_yaml_dir, yaml_file)) as f:
            data = yaml.load(f.read())
            v_release = find_highest_release_version(data['releases'])

        # do some mapping if pkg name is different to the name from release repo
        if project_name in projects_mapping:
            project_name_pkg = projects_mapping[project_name]
        else:
            project_name_pkg = project_name

        # path to the corresponding .spec.j2 file
        rpm_packaging_pkg_project_spec = os.path.join(
            args['rpm-packaging-git-dir'],
            'openstack', project_name_pkg,
            '%s.spec.j2' % project_name_pkg)
        v_rpm_packaging_pkg = find_rpm_packaging_pkg_version(rpm_packaging_pkg_project_spec)

        # add both versions to the project dict
        projects[project_name] = V(v_release, v_rpm_packaging_pkg)

    if args['format'] == 'text':
        output_text(args['release'], projects)
    elif args['format'] == 'html':
        output_html(args['release'], projects)

    return 0


if __name__ == '__main__':
    sys.exit(main())
