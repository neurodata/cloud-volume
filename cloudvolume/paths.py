from collections import namedtuple
import os
import posixpath
import re
import sys

from .exceptions import UnsupportedProtocolError
from .lib import yellow, toabs

ExtractedPath = namedtuple('ExtractedPath', 
  ('format', 'protocol', 'bucket', 'basepath', 'no_bucket_basepath', 'dataset', 'layer')
)

ALLOWED_PROTOCOLS = [ 'gs', 'file', 's3', 'matrix', 'http', 'https' ]
ALLOWED_FORMATS = [ 'graphene', 'precomputed', 'boss' ] 

CLOUDPATH_ERROR = yellow("""
Cloud Path must conform to FORMAT://PROTOCOL://BUCKET/PATH
Examples: 
  precomputed://gs://test_bucket/em
  gs://test_bucket/em
  graphene://https://example.com/image/em

Supported Formats: None (precomputed), {}
Supported Protocols: {}

Cloud Path Recieved: {}
""").format(
  ", ".join(ALLOWED_FORMATS), ", ".join(ALLOWED_PROTOCOLS), '{}' # curry first two
)

def ascloudpath(epath):
  return "{}://{}://{}".format(
    epath.format, epath.protocol, 
    posixpath.join(epath.basepath, epath.dataset, epath.layer)
  )

def pop_protocol(cloudpath):
  protocol_re = re.compile(r'(\w+)://')

  match = re.match(protocol_re, cloudpath)

  if not match:
    return (None, cloudpath)

  (protocol,) = match.groups()
  cloudpath = re.sub(protocol_re, '', cloudpath, count=1)

  return (protocol, cloudpath)

def extract_format_protocol(cloudpath):
  error = UnsupportedProtocolError(CLOUDPATH_ERROR.format(cloudpath))
  
  (proto, cloudpath) = pop_protocol(cloudpath)
  
  if proto is None:
    raise error # e.g. ://test_bucket, test_bucket, wow//test_bucket

  fmt, protocol = None, None

  if proto in ALLOWED_PROTOCOLS:
    fmt = 'precomputed'
    protocol = proto 
  elif proto in ALLOWED_FORMATS:
    fmt = proto

  (proto, cloudpath) = pop_protocol(cloudpath)

  if proto in ALLOWED_FORMATS:
    raise error # e.g. gs://graphene://
  elif proto in ALLOWED_PROTOCOLS:
    if protocol is None:
      protocol = proto
    else:
      raise error # e.g. gs://gs:// 

  (proto, cloudpath) = pop_protocol(cloudpath)
  if proto is not None:
    raise error # e.g. gs://gs://gs://

  return (fmt, protocol, cloudpath)

def strict_extract(cloudpath, windows=None, disable_toabs=False):
  """
  Same as cloudvolume.paths.extract, but raise an additional 
  cloudvolume.exceptions.UnsupportedProtocolError
  if either dataset or layer is not set.

  Returns: ExtractedPath
  """
  path = extract(cloudpath, windows, disable_toabs)

  if path.dataset == '' or path.layer == '':
    raise UnsupportedProtocolError(CLOUDPATH_ERROR.format(cloudpath))

  return path

def extract(cloudpath, windows=None, disable_toabs=False):
  """
  Given a valid cloudpath of the form 
  format://protocol://bucket/.../dataset/layer

  Where format in: None, precomputed, boss, graphene
  Where protocol in: None, file, gs, s3, http(s), matrix

  Return an ExtractedPath which breaks out the components
  format, protocol, bucket, path, intermediate_path, dataset, layer

  Raise a cloudvolume.exceptions.UnsupportedProtocolError if the
  path does not conform to a valid path.

  Windows OS may handle file protocol paths slightly differently
  than other OSes.

  Returns: ExtractedPath
  """
  if len(cloudpath) == 0:
    return ExtractedPath('','','','','','','')

  windows_file_re = re.compile(r'((?:\w:\\)[\d\w_\.\-]+(?:\\)?)') # for C:\what\a\great\path
  bucket_re = re.compile(r'^(/?[~\d\w_\.\-]+(?::\d+)?)/') # posix /what/a/great/path
  
  error = UnsupportedProtocolError(CLOUDPATH_ERROR.format(cloudpath))

  if windows is None:
    windows = sys.platform == 'win32'

  if disable_toabs:
    abspath = lambda x: x # can't prepend linux paths when force testing windows
  else:
    abspath = toabs    

  fmt, protocol, cloudpath = extract_format_protocol(cloudpath)
  
  split_char = '/'
  if protocol == 'file':
    cloudpath = abspath(cloudpath)
    if windows:
      bucket_re = windows_file_re
    split_char = os.path.sep

  match = re.match(bucket_re, cloudpath)
  if not match:
    raise error

  (bucket,) = match.groups()

  splitcloudpath = cloudpath 
  if splitcloudpath[0] == split_char:
    splitcloudpath = splitcloudpath[1:]
  if splitcloudpath[-1] == split_char:
    splitcloudpath = splitcloudpath[:-1]

  splitties = splitcloudpath.split(split_char)
  if len(splitties) == 0:
    return ExtractedPath(fmt, protocol, bucket, cloudpath, '', bucket, '')
  elif len(splitties) == 1:
    dataset = bucket
    layer = splitties[0]
    basepath = split_char.join(splitties[:-1])
    no_bucket_basepath = split_char.join(splitties[1:-1])
  elif len(splitties) >= 2:
    dataset, layer = splitties[-2:]
    no_bucket_basepath = split_char.join(splitties[1:-1])
    basepath = split_char.join([bucket] + splitties[1:-1])

  return ExtractedPath(
    fmt, protocol, bucket, 
    basepath, no_bucket_basepath, 
    dataset, layer
  )

def to_https_protocol(cloudpath):
  cloudpath = cloudpath.replace("gs://", "https://storage.googleapis.com/", 1)
  cloudpath = cloudpath.replace("s3://", "https://s3.amazonaws.com/", 1)
  cloudpath = cloudpath.replace("matrix://", "https://s3-hpcrc.rc.princeton.edu/", 1)
  return cloudpath
