# Copyright 2018, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Directories with `tor descriptor information
<../tutorials/mirror_mirror_on_the_wall.html>`_. At a very high level tor works
as follows...

1. Volunteer starts a new tor relay, during which it sends a `server
   descriptor <descriptor/server_descriptor.html>`_ to each of the directory
   authorities.

2. Each hour the directory authorities make a `vote
   <descriptor/networkstatus.html>`_  that says who they think the active
   relays are in the network and some attributes about them.

3. The directory authorities send each other their votes, and compile that
   into the `consensus <descriptor/networkstatus.html>`_. This document is very
   similar to the votes, the only difference being that the majority of the
   authorities agree upon and sign this document. The idividual relay entries
   in the vote or consensus is called `router status entries
   <descriptor/router_status_entry.html>`_.

4. Tor clients (people using the service) download the consensus from an
   authority, fallback, or other mirror to determine who the active relays in
   the network are. They then use this to construct circuits and use the
   network.

::

  Directory - Relay we can retrieve descriptor information from
    | |- from_cache - Provides cached information bundled with Stem.
    | +- from_remote - Downloads the latest directory information from tor.
    |
    |- Authority - Tor directory authority
    +- Fallback - Mirrors that can be used instead of the authorities

.. versionadded:: 1.7.0
"""

import os
import re
import sys

import stem.util.conf

from stem.util import _hash_attr, connection, str_tools, tor_tools

try:
  # added in python 2.7
  from collections import OrderedDict
except ImportError:
  from stem.util.ordereddict import OrderedDict

try:
  # account for urllib's change between python 2.x and 3.x
  import urllib.request as urllib
except ImportError:
  import urllib2 as urllib

GITWEB_AUTHORITY_URL = 'https://gitweb.torproject.org/tor.git/plain/src/or/auth_dirs.inc'
GITWEB_FALLBACK_URL = 'https://gitweb.torproject.org/tor.git/plain/src/or/fallback_dirs.inc'
CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cached_fallbacks.cfg')

AUTHORITY_NAME = re.compile('"(\S+) orport=(\d+) .*"')
AUTHORITY_V3IDENT = re.compile('"v3ident=([\dA-F]{40}) "')
AUTHORITY_IPV6 = re.compile('"ipv6=\[([\da-f:]+)\]:(\d+) "')
AUTHORITY_ADDR = re.compile('"([\d\.]+):(\d+) ([\dA-F ]{49})",')

FALLBACK_DIV = '/* ===== */'
FALLBACK_MAPPING = re.compile('/\*\s+(\S+)=(\S*)\s+\*/')

FALLBACK_ADDR = re.compile('"([\d\.]+):(\d+) orport=(\d+) id=([\dA-F]{40}).*')
FALLBACK_NICKNAME = re.compile('/\* nickname=(\S+) \*/')
FALLBACK_EXTRAINFO = re.compile('/\* extrainfo=([0-1]) \*/')
FALLBACK_IPV6 = re.compile('" ipv6=\[([\da-f:]+)\]:(\d+)"')


class Directory(object):
  """
  Relay we can contact for directory information.

  Our :func:`~stem.directory.Directory.from_cache` and
  :func:`~stem.directory.Directory.from_remote` functions key off a
  different identifier based on our subclass...

    * **Authority** keys off the nickname.
    * **Fallback** keys off fingerprints.

  This is because authorities are highly static and canonically known by their
  names, whereas fallbacks vary more and don't necessarily have a nickname to
  key off of.

  .. versionchanged:: 1.3.0
     Moved nickname from subclasses to this base class.

  :var str address: IPv4 address of the directory
  :var int or_port: port on which the relay services relay traffic
  :var int dir_port: port on which directory information is available
  :var str fingerprint: relay fingerprint
  :var str nickname: relay nickname
  """

  def __init__(self, address, or_port, dir_port, fingerprint, nickname):
    self.address = address
    self.or_port = or_port
    self.dir_port = dir_port
    self.fingerprint = fingerprint
    self.nickname = nickname

  @staticmethod
  def from_cache():
    """
    Provides cached Tor directory information. This information is hardcoded
    into Tor and occasionally changes, so the information this provides might
    not necessarily match your version of tor.

    .. versionadded:: 1.5.0

    .. versionchanged:: 1.7.0
       Support added to the :class:`~stem.directory.Authority` class.

    :returns: **dict** of **str** identifiers to
      :class:`~stem.directory.Directory` instances
    """

    raise NotImplementedError('Unsupported Operation: this should be implemented by the Directory subclass')

  @staticmethod
  def from_remote(timeout = 60):
    """
    Reads and parses tor's directory data `from gitweb.torproject.org <https://gitweb.torproject.org/>`_.
    Note that while convenient, this reliance on GitWeb means you should alway
    call with a fallback, such as...

    ::

      try:
        authorities = stem.directory.Authority.from_remote()
      except IOError:
        authorities = stem.directory.Authority.from_cache()

    .. versionadded:: 1.5.0

    .. versionchanged:: 1.7.0
       Support added to the :class:`~stem.directory.Authority` class.

    :param int timeout: seconds to wait before timing out the request

    :returns: **dict** of **str** identifiers to their
      :class:`~stem.directory.Directory`

    :raises: **IOError** if unable to retrieve the fallback directories
    """

    raise NotImplementedError('Unsupported Operation: this should be implemented by the Directory subclass')

  def __hash__(self):
    return _hash_attr(self, 'address', 'or_port', 'dir_port', 'fingerprint')

  def __eq__(self, other):
    return hash(self) == hash(other) if isinstance(other, Directory) else False

  def __ne__(self, other):
    return not self == other


class Authority(Directory):
  """
  Tor directory authority, a special type of relay `hardcoded into tor
  <https://gitweb.torproject.org/tor.git/plain/src/or/auth_dirs.inc>`_
  that enumerates the other relays within the network.

  .. versionchanged:: 1.3.0
     Added the is_bandwidth_authority attribute.

  :var str v3ident: identity key fingerprint used to sign votes and consensus
  :var bool is_bandwidth_authority: **True** if this is a bandwidth authority,
    **False** otherwise
  """

  def __init__(self, address = None, or_port = None, dir_port = None, fingerprint = None, nickname = None, v3ident = None, is_bandwidth_authority = False):
    super(Authority, self).__init__(address, or_port, dir_port, fingerprint, nickname)
    self.v3ident = v3ident
    self.is_bandwidth_authority = is_bandwidth_authority

  @staticmethod
  def from_cache():
    return dict(DIRECTORY_AUTHORITIES)

  @staticmethod
  def from_remote(timeout = 60):
    try:
      lines = str_tools._to_unicode(urllib.urlopen(GITWEB_AUTHORITY_URL, timeout = timeout).read()).splitlines()
    except:
      exc = sys.exc_info()[1]
      raise IOError("Unable to download tor's directory authorities from %s: %s" % (GITWEB_AUTHORITY_URL, exc))

    if not lines:
      raise IOError('%s did not have any content' % GITWEB_AUTHORITY_URL)

    results = {}

    while lines:
      section = Authority._pop_section(lines)

      if section:
        try:
          authority = Authority._from_str('\n'.join(section))
          results[authority.nickname] = authority
        except ValueError as exc:
          raise IOError(str(exc))

    return results

  @staticmethod
  def _from_str(content):
    """
    Parses authority from its textual representation. For example...

    ::

      "moria1 orport=9101 "
        "v3ident=D586D18309DED4CD6D57C18FDB97EFA96D330566 "
        "128.31.0.39:9131 9695 DFC3 5FFE B861 329B 9F1A B04C 4639 7020 CE31",

    :param str content: text to parse

    :returns: :class:`~stem.directory.Authority` in the text

    :raises: **ValueError** if content is malformed
    """

    if isinstance(content, bytes):
      content = str_tools._to_unicode(content)

    matches = {}

    for line in content.splitlines():
      for matcher in (AUTHORITY_NAME, AUTHORITY_V3IDENT, AUTHORITY_IPV6, AUTHORITY_ADDR):
        m = matcher.match(line.strip())

        if m:
          match_groups = m.groups()
          matches[matcher] = match_groups if len(match_groups) > 1 else match_groups[0]

    if AUTHORITY_NAME not in matches:
      raise ValueError('Unable to parse the name and orport from:\n\n%s' % content)
    elif AUTHORITY_ADDR not in matches:
      raise ValueError('Unable to parse the address and fingerprint from:\n\n%s' % content)

    nickname, or_port = matches.get(AUTHORITY_NAME)
    v3ident = matches.get(AUTHORITY_V3IDENT)
    orport_v6 = matches.get(AUTHORITY_IPV6)  # TODO: add this to stem's data?
    address, dir_port, fingerprint = matches.get(AUTHORITY_ADDR)

    fingerprint = fingerprint.replace(' ', '')

    if not connection.is_valid_ipv4_address(address):
      raise ValueError('%s has an invalid IPv4 address: %s' % (nickname, address))
    elif not connection.is_valid_port(or_port):
      raise ValueError('%s has an invalid or_port: %s' % (nickname, or_port))
    elif not connection.is_valid_port(dir_port):
      raise ValueError('%s has an invalid dir_port: %s' % (nickname, dir_port))
    elif not tor_tools.is_valid_fingerprint(fingerprint):
      raise ValueError('%s has an invalid fingerprint: %s' % (nickname, fingerprint))
    elif nickname and not tor_tools.is_valid_nickname(nickname):
      raise ValueError('%s has an invalid nickname: %s' % (nickname, nickname))
    elif orport_v6 and not connection.is_valid_ipv6_address(orport_v6[0]):
      raise ValueError('%s has an invalid IPv6 address: %s' % (nickname, orport_v6[0]))
    elif orport_v6 and not connection.is_valid_port(orport_v6[1]):
      raise ValueError('%s has an invalid ORPort for its IPv6 endpoint: %s' % (nickname, orport_v6[1]))
    elif v3ident and not tor_tools.is_valid_fingerprint(v3ident):
      raise ValueError('%s has an invalid v3ident: %s' % (nickname, v3ident))

    return Authority(
      address = address,
      or_port = int(or_port),
      dir_port = int(dir_port),
      fingerprint = fingerprint,
      nickname = nickname,
      v3ident = v3ident,
    )

  @staticmethod
  def _pop_section(lines):
    """
    Provides the next authority entry.
    """

    section_lines = []

    if lines:
      section_lines.append(lines.pop(0))

      while lines and lines[0].startswith(' '):
        section_lines.append(lines.pop(0))

    return section_lines

  def __hash__(self):
    return _hash_attr(self, 'nickname', 'v3ident', 'is_bandwidth_authority', parent = Directory)

  def __eq__(self, other):
    return hash(self) == hash(other) if isinstance(other, Authority) else False

  def __ne__(self, other):
    return not self == other


class Fallback(Directory):
  """
  Particularly stable relays tor can instead of authorities when
  bootstrapping. These relays are `hardcoded in tor
  <https://gitweb.torproject.org/tor.git/tree/src/or/fallback_dirs.inc>`_.

  For example, the following checks the performance of tor's fallback directories...

  ::

    import time
    from stem.descriptor.remote import get_consensus
    from stem.directory import Fallback

    for fallback in Fallback.from_cache().values():
      start = time.time()
      get_consensus(endpoints = [(fallback.address, fallback.dir_port)]).run()
      print('Downloading the consensus took %0.2f from %s' % (time.time() - start, fallback.fingerprint))

  ::

    % python example.py
    Downloading the consensus took 5.07 from 0AD3FA884D18F89EEA2D89C019379E0E7FD94417
    Downloading the consensus took 3.59 from C871C91489886D5E2E94C13EA1A5FDC4B6DC5204
    Downloading the consensus took 4.16 from 74A910646BCEEFBCD2E874FC1DC997430F968145
    ...

  .. versionadded:: 1.5.0

  .. versionchanged:: 1.7.0
     Added the nickname, has_extrainfo, and header attributes which are part of
     the `second version of the fallback directories
     <https://lists.torproject.org/pipermail/tor-dev/2017-December/012721.html>`_.

  :var bool has_extrainfo: **True** if the relay should be able to provide
    extrainfo descriptors, **False** otherwise.
  :var str orport_v6: **(address, port)** tuple for the directory's IPv6
    ORPort, or **None** if it doesn't have one
  :var dict header: metadata about the fallback directory file this originated from
  """

  def __init__(self, address = None, or_port = None, dir_port = None, fingerprint = None, nickname = None, has_extrainfo = False, orport_v6 = None, header = None):
    super(Fallback, self).__init__(address, or_port, dir_port, fingerprint, nickname)

    self.has_extrainfo = has_extrainfo
    self.orport_v6 = orport_v6
    self.header = header if header else OrderedDict()

  @staticmethod
  def from_cache(path = CACHE_PATH):
    conf = stem.util.conf.Config()
    conf.load(path)
    headers = OrderedDict([(k.split('.', 1)[1], conf.get(k)) for k in conf.keys() if k.startswith('header.')])

    results = {}

    for fingerprint in set([key.split('.')[0] for key in conf.keys()]):
      if fingerprint in ('tor_commit', 'stem_commit', 'header'):
        continue

      attr = {}

      for attr_name in ('address', 'or_port', 'dir_port', 'nickname', 'has_extrainfo', 'orport6_address', 'orport6_port'):
        key = '%s.%s' % (fingerprint, attr_name)
        attr[attr_name] = conf.get(key)

        if not attr[attr_name] and attr_name not in ('nickname', 'has_extrainfo', 'orport6_address', 'orport6_port'):
          raise IOError("'%s' is missing from %s" % (key, CACHE_PATH))

      if not connection.is_valid_ipv4_address(attr['address']):
        raise IOError("'%s.address' was an invalid IPv4 address (%s)" % (fingerprint, attr['address']))
      elif not connection.is_valid_port(attr['or_port']):
        raise IOError("'%s.or_port' was an invalid port (%s)" % (fingerprint, attr['or_port']))
      elif not connection.is_valid_port(attr['dir_port']):
        raise IOError("'%s.dir_port' was an invalid port (%s)" % (fingerprint, attr['dir_port']))
      elif attr['nickname'] and not tor_tools.is_valid_nickname(attr['nickname']):
        raise IOError("'%s.nickname' was an invalid nickname (%s)" % (fingerprint, attr['nickname']))
      elif attr['orport6_address'] and not connection.is_valid_ipv6_address(attr['orport6_address']):
        raise IOError("'%s.orport6_address' was an invalid IPv6 address (%s)" % (fingerprint, attr['orport6_address']))
      elif attr['orport6_port'] and not connection.is_valid_port(attr['orport6_port']):
        raise IOError("'%s.orport6_port' was an invalid port (%s)" % (fingerprint, attr['orport6_port']))

      if attr['orport6_address'] and attr['orport6_port']:
        orport_v6 = (attr['orport6_address'], int(attr['orport6_port']))
      else:
        orport_v6 = None

      results[fingerprint] = Fallback(
        address = attr['address'],
        or_port = int(attr['or_port']),
        dir_port = int(attr['dir_port']),
        fingerprint = fingerprint,
        nickname = attr['nickname'],
        has_extrainfo = attr['has_extrainfo'] == 'true',
        orport_v6 = orport_v6,
        header = headers,
      )

    return results

  @staticmethod
  def from_remote(timeout = 60):
    try:
      lines = str_tools._to_unicode(urllib.urlopen(GITWEB_FALLBACK_URL, timeout = timeout).read()).splitlines()
    except:
      exc = sys.exc_info()[1]
      raise IOError("Unable to download tor's fallback directories from %s: %s" % (GITWEB_FALLBACK_URL, exc))

    if not lines:
      raise IOError('%s did not have any content' % GITWEB_FALLBACK_URL)
    elif lines[0] != '/* type=fallback */':
      raise IOError('%s does not have a type field indicating it is fallback directory metadata' % GITWEB_FALLBACK_URL)

    # header metadata

    header = {}

    for line in Fallback._pop_section(lines):
      mapping = FALLBACK_MAPPING.match(line)

      if mapping:
        header[mapping.group(1)] = mapping.group(2)
      else:
        raise IOError('Malformed fallback directory header line: %s' % line)

    # human readable comments

    Fallback._pop_section(lines)

    # content, everything remaining are fallback directories

    results = {}

    while lines:
      section = Fallback._pop_section(lines)

      if section:
        try:
          fallback = Fallback._from_str('\n'.join(section))
          fallback.header = header
          results[fallback.fingerprint] = fallback
        except ValueError as exc:
          raise IOError(str(exc))

    return results

  @staticmethod
  def _from_str(content):
    """
    Parses a fallback from its textual representation. For example...

    ::

      "5.9.110.236:9030 orport=9001 id=0756B7CD4DFC8182BE23143FAC0642F515182CEB"
      " ipv6=[2a01:4f8:162:51e2::2]:9001"
      /* nickname=rueckgrat */
      /* extrainfo=1 */

    :param str content: text to parse

    :returns: :class:`~stem.directory.Fallback` in the text

    :raises: **ValueError** if content is malformed
    """

    if isinstance(content, bytes):
      content = str_tools._to_unicode(content)

    matches = {}

    for line in content.splitlines():
      for matcher in (FALLBACK_ADDR, FALLBACK_NICKNAME, FALLBACK_EXTRAINFO, FALLBACK_IPV6):
        m = matcher.match(line)

        if m:
          match_groups = m.groups()
          matches[matcher] = match_groups if len(match_groups) > 1 else match_groups[0]

    if FALLBACK_ADDR not in matches:
      raise ValueError('Malformed fallback address line:\n\n%s' % content)

    address, dir_port, or_port, fingerprint = matches[FALLBACK_ADDR]
    nickname = matches.get(FALLBACK_NICKNAME)
    has_extrainfo = matches.get(FALLBACK_EXTRAINFO) == '1'
    orport_v6 = matches.get(FALLBACK_IPV6)

    if not connection.is_valid_ipv4_address(address):
      raise ValueError('%s has an invalid IPv4 address: %s' % (fingerprint, address))
    elif not connection.is_valid_port(or_port):
      raise ValueError('%s has an invalid or_port: %s' % (fingerprint, or_port))
    elif not connection.is_valid_port(dir_port):
      raise ValueError('%s has an invalid dir_port: %s' % (fingerprint, dir_port))
    elif not tor_tools.is_valid_fingerprint(fingerprint):
      raise ValueError('%s has an invalid fingerprint: %s' % (fingerprint, fingerprint))
    elif nickname and not tor_tools.is_valid_nickname(nickname):
      raise ValueError('%s has an invalid nickname: %s' % (fingerprint, nickname))
    elif orport_v6 and not connection.is_valid_ipv6_address(orport_v6[0]):
      raise ValueError('%s has an invalid IPv6 address: %s' % (fingerprint, orport_v6[0]))
    elif orport_v6 and not connection.is_valid_port(orport_v6[1]):
      raise ValueError('%s has an invalid ORPort for its IPv6 endpoint: %s' % (fingerprint, orport_v6[1]))

    return Fallback(
      address = address,
      or_port = int(or_port),
      dir_port = int(dir_port),
      fingerprint = fingerprint,
      nickname = nickname,
      has_extrainfo = has_extrainfo,
      orport_v6 = (orport_v6[0], int(orport_v6[1])) if orport_v6 else None,
    )

  @staticmethod
  def _pop_section(lines):
    """
    Provides lines up through the next divider. This excludes lines with just a
    comma since they're an artifact of these being C strings.
    """

    section_lines = []

    if lines:
      line = lines.pop(0)

      while lines and line != FALLBACK_DIV:
        if line.strip() != ',':
          section_lines.append(line)

        line = lines.pop(0)

    return section_lines

  @staticmethod
  def _write(fallbacks, tor_commit, stem_commit, headers, path = CACHE_PATH):
    """
    Persists fallback directories to a location in a way that can be read by
    from_cache().

    :param dict fallbacks: mapping of fingerprints to their fallback directory
    :param str tor_commit: tor commit the fallbacks came from
    :param str stem_commit: stem commit the fallbacks came from
    :param dict headers: metadata about the file these came from
    :param str path: location fallbacks will be persisted to
    """

    conf = stem.util.conf.Config()
    conf.set('tor_commit', tor_commit)
    conf.set('stem_commit', stem_commit)

    for k, v in headers.items():
      conf.set('header.%s' % k, v)

    for directory in sorted(fallbacks.values(), key = lambda x: x.fingerprint):
      fingerprint = directory.fingerprint
      conf.set('%s.address' % fingerprint, directory.address)
      conf.set('%s.or_port' % fingerprint, str(directory.or_port))
      conf.set('%s.dir_port' % fingerprint, str(directory.dir_port))
      conf.set('%s.nickname' % fingerprint, directory.nickname)
      conf.set('%s.has_extrainfo' % fingerprint, 'true' if directory.has_extrainfo else 'false')

      if directory.orport_v6:
        conf.set('%s.orport6_address' % fingerprint, str(directory.orport_v6[0]))
        conf.set('%s.orport6_port' % fingerprint, str(directory.orport_v6[1]))

    conf.save(path)

  def __hash__(self):
    return _hash_attr(self, 'address', 'or_port', 'dir_port', 'fingerprint', 'nickname', 'has_extrainfo', 'orport_v6', 'header', parent = Directory)

  def __eq__(self, other):
    return hash(self) == hash(other) if isinstance(other, Fallback) else False

  def __ne__(self, other):
    return not self == other


def _fallback_directory_differences(previous_directories, new_directories):
  """
  Provides a description of how fallback directories differ.
  """

  lines = []

  added_fp = set(new_directories.keys()).difference(previous_directories.keys())
  removed_fp = set(previous_directories.keys()).difference(new_directories.keys())

  for fp in added_fp:
    directory = new_directories[fp]
    orport_v6 = '%s:%s' % directory.orport_v6 if directory.orport_v6 else '[none]'

    lines += [
      '* Added %s as a new fallback directory:' % directory.fingerprint,
      '  address: %s' % directory.address,
      '  or_port: %s' % directory.or_port,
      '  dir_port: %s' % directory.dir_port,
      '  nickname: %s' % directory.nickname,
      '  has_extrainfo: %s' % directory.has_extrainfo,
      '  orport_v6: %s' % orport_v6,
      '',
    ]

  for fp in removed_fp:
    lines.append('* Removed %s as a fallback directory' % fp)

  for fp in new_directories:
    if fp in added_fp or fp in removed_fp:
      continue  # already discussed these

    previous_directory = previous_directories[fp]
    new_directory = new_directories[fp]

    if previous_directory != new_directory:
      for attr in ('address', 'or_port', 'dir_port', 'fingerprint', 'orport_v6'):
        old_attr = getattr(previous_directory, attr)
        new_attr = getattr(new_directory, attr)

        if old_attr != new_attr:
          lines.append('* Changed the %s of %s from %s to %s' % (attr, fp, old_attr, new_attr))

  return '\n'.join(lines)


DIRECTORY_AUTHORITIES = {
  'moria1': Authority(
    nickname = 'moria1',
    address = '128.31.0.39',
    or_port = 9101,
    dir_port = 9131,
    is_bandwidth_authority = True,
    fingerprint = '9695DFC35FFEB861329B9F1AB04C46397020CE31',
    v3ident = 'D586D18309DED4CD6D57C18FDB97EFA96D330566',
  ),
  'tor26': Authority(
    nickname = 'tor26',
    address = '86.59.21.38',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = False,
    fingerprint = '847B1F850344D7876491A54892F904934E4EB85D',
    v3ident = '14C131DFC5C6F93646BE72FA1401C02A8DF2E8B4',
  ),
  'dizum': Authority(
    nickname = 'dizum',
    address = '194.109.206.212',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = False,
    fingerprint = '7EA6EAD6FD83083C538F44038BBFA077587DD755',
    v3ident = 'E8A9C45EDE6D711294FADF8E7951F4DE6CA56B58',
  ),
  'gabelmoo': Authority(
    nickname = 'gabelmoo',
    address = '131.188.40.189',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = True,
    fingerprint = 'F2044413DAC2E02E3D6BCF4735A19BCA1DE97281',
    v3ident = 'ED03BB616EB2F60BEC80151114BB25CEF515B226',
  ),
  'dannenberg': Authority(
    nickname = 'dannenberg',
    address = '193.23.244.244',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = False,
    fingerprint = '7BE683E65D48141321C5ED92F075C55364AC7123',
    v3ident = '0232AF901C31A04EE9848595AF9BB7620D4C5B2E',
  ),
  'maatuska': Authority(
    nickname = 'maatuska',
    address = '171.25.193.9',
    or_port = 80,
    dir_port = 443,
    is_bandwidth_authority = True,
    fingerprint = 'BD6A829255CB08E66FBE7D3748363586E46B3810',
    v3ident = '49015F787433103580E3B66A1707A00E60F2D15B',
  ),
  'Faravahar': Authority(
    nickname = 'Faravahar',
    address = '154.35.175.225',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = True,
    fingerprint = 'CF6D0AAFB385BE71B8E111FC5CFF4B47923733BC',
    v3ident = 'EFCBE720AB3A82B99F9E953CD5BF50F7EEFC7B97',
  ),
  'longclaw': Authority(
    nickname = 'longclaw',
    address = '199.58.81.140',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = False,
    fingerprint = '74A910646BCEEFBCD2E874FC1DC997430F968145',
    v3ident = '23D15D965BC35114467363C165C4F724B64B4F66',
  ),
  'bastet': Authority(
    nickname = 'bastet',
    address = '204.13.164.118',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = True,
    fingerprint = '24E2F139121D4394C54B5BCC368B3B411857C413',
    v3ident = '27102BC123E7AF1D4741AE047E160C91ADC76B21',
  ),
  'Bifroest': Authority(
    nickname = 'Bifroest',
    address = '37.218.247.217',
    or_port = 443,
    dir_port = 80,
    is_bandwidth_authority = False,
    fingerprint = '1D8F3A91C37C5D1C4C19B1AD1D0CFBE8BF72D8E1',
    v3ident = None,  # does not vote in the consensus
  ),
}
