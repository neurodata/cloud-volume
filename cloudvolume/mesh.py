import copy
import struct

import numpy as np

from .exceptions import MeshDecodeError
from .lib import yellow

NOTICE = {
  'vertices': 0,
  'num_vertices': 0,
  'faces': 0,
}

def deprecation_notice(key):
  if NOTICE[key] < 1:
    print(yellow("""
  Deprecation Notice: Meshes, formerly dicts, are now PrecomputedMesh objects
  as of CloudVolume 0.51.0. 

  Please change mesh['{}'] to mesh.{}
  """.format(key, key)))
    NOTICE[key] += 1


def dist_to_chunk(v, chunk_size):
      plus_one_dist = np.mod(v, chunk_size)
      minus_one_dist = chunk_size - plus_one_dist
      return np.minimum(plus_one_dist, minus_one_dist)
  
def is_draco_chunk_aligned(verts, chunk_size, draco_grid_size=21):
    d_me = dist_to_chunk(verts, chunk_size)
    d_up = dist_to_chunk(verts+draco_grid_size, chunk_size)
    d_down = dist_to_chunk(verts-draco_grid_size, chunk_size)
    is_draco_grid = np.any((d_me<d_down) &(d_me<d_up) & (d_me<draco_grid_size), axis=1)
    return is_draco_grid
    

class Mesh(object):
  """
  Represents the vertices, faces, and normals of a mesh
  as numpy arrays.

  class PrecomputedMesh:
    ndarray[float32, ndim=2] self.vertices: [ [x,y,z], ... ]
    ndarray[uint32,  ndim=2] self.faces:    [ [v1,v2,v3], ... ]
    ndarray[float32, ndim=2] self.normals:  [ [nx,ny,nz], ... ]

  """
  def __init__(
    self, vertices, faces, normals=None, 
    segid=None, encoding_type=None, encoding_options=None
  ):
    self.vertices = np.array(vertices, dtype=np.float32)
    self.faces = np.array(faces, dtype=np.uint32)

    if normals is None:
      self.normals = np.array([], dtype=np.float32).reshape((0,3))
    else:
      self.normals = np.array(normals, dtype=np.float32)

    self.segid = segid
    self.encoding_type = encoding_type
    self.encoding_options = encoding_options 

  def __len__(self):
    return self.vertices.shape[0]

  def __eq__(self, other):
    """Tests strict equality between two meshes."""

    no_self_normals = self.normals is None or self.normals.size == 0
    no_other_normals = other.normals is None or other.normals.size == 0

    if no_self_normals != no_other_normals:
      return False
       
    equality = np.all(self.vertices == other.vertices) \
      and np.all(self.faces == other.faces)

    if no_self_normals:
      return equality

    return (equality and np.all(self.normals == other.normals))

  def __repr__(self):
    return "Mesh(vertices<{}>, faces<{}>, normals<{}>, segid={}, encoding_type=<{}>)".format(
      self.vertices.shape[0], self.faces.shape[0], self.normals.shape[0],
      self.segid, self.encoding_type
    )

  def __getitem__(self, key):
    val = None 
    if key == 'vertices':
      val = self.vertices
    elif key == 'num_vertices':
      val = len(self)
    elif key == 'faces':
      val = self.faces
    else:
      raise KeyError("{} not found.".format(key))

    deprecation_notice(key)
    return val

  def empty(self):
    return self.vertices.size == 0 or self.faces.size == 0

  def clone(self):
    return Mesh(
      np.copy(self.vertices), np.copy(self.faces), np.copy(self.normals),
      self.segid, 
      encoding_type=copy.deepcopy(self.encoding_type),
      encoding_options=copy.deepcopy(self.encoding_options),
    )

  def triangles(self):
    Nf = self.faces.shape[0]
    tris = np.zeros( (Nf, 3, 3), dtype=np.float32, order='C' ) # triangle, vertices, (x,y,z)

    for i in range(Nf):
      for j in range(3):
        tris[i,j,:] = self.vertices[ self.faces[i,j] ]

    return tris

  @classmethod
  def concatenate(cls, *meshes):
    vertex_ct = np.zeros(len(meshes) + 1, np.uint32)
    vertex_ct[1:] = np.cumsum([ len(mesh) for mesh in meshes ])

    vertices = np.concatenate([ mesh.vertices for mesh in meshes ])
    
    faces = np.concatenate([ 
      mesh.faces + vertex_ct[i] for i, mesh in enumerate(meshes) 
    ])

    normals = np.concatenate([ mesh.normals for mesh in meshes ])

    encoding_type = list(set([ mesh.encoding_type for mesh in meshes ]))
    if len(encoding_type) == 1:
      encoding_type = encoding_type[0]

    return Mesh(vertices, faces, normals, encoding_type=encoding_type)

  def consolidate(self):
    """Remove duplicate vertices and faces. Returns a new mesh object."""
    if self.empty():
      return Mesh([], [], normals=None)

    vertices = self.vertices
    faces = self.faces
    normals = self.normals

    eff_verts, uniq_idx, idx_representative = np.unique(
      vertices, axis=0, return_index=True, return_inverse=True
    )

    face_vector_map = np.vectorize(lambda x: idx_representative[x])
    eff_faces = face_vector_map(faces)
    eff_faces = np.unique(eff_faces, axis=0)

    # normal_vector_map = np.vectorize(lambda idx: normals[idx])
    # eff_normals = normal_vector_map(uniq_idx)

    return Mesh(eff_verts, eff_faces, None, 
      segid=self.segid,
      encoding_type=copy.deepcopy(self.encoding_type),
      encoding_options=copy.deepcopy(self.encoding_options),
    )

  @classmethod
  def from_precomputed(self, binary):
    """
    Mesh from_precomputed(self, binary)

    Decode a Precomputed format mesh from a byte string.
    
    Format:
      uint32        Nv * float32 * 3   uint32 * 3 until end
      Nv            (x,y,z)            (v1,v2,v2)
      N Vertices    Vertices           Faces
    """
    num_vertices = struct.unpack("=I", binary[0:4])[0]
    try:
      # count=-1 means all data in buffer
      vertices = np.frombuffer(binary, dtype=np.float32, count=3*num_vertices, offset=4)
      faces = np.frombuffer(binary, dtype=np.uint32, count=-1, offset=(4 + 12 * num_vertices)) 
    except ValueError:
      raise MeshDecodeError("""
        The input buffer is too small for the Precomputed format.
        Minimum Bytes: {} 
        Actual Bytes: {}
      """.format(4 + 4 * num_vertices, len(binary)))

    vertices = vertices.reshape(num_vertices, 3)
    faces = faces.reshape(faces.size // 3, 3)

    return Mesh(
      vertices, faces, normals=None, 
      encoding_type='precomputed'
    )

  def to_precomputed(self):
    """
    bytes to_precomputed(self)

    Convert mesh into binary format compatible with Neuroglancer.
    Does not preserve normals.
    """
    vertex_index_format = [
      np.uint32(self.vertices.shape[0]), # Number of vertices (3 coordinates)
      self.vertices,
      self.faces
    ]
    return b''.join([ array.tobytes('C') for array in vertex_index_format ])

  @classmethod
  def from_obj(self, text):
    """Given a string representing a Wavefront OBJ file, decode to a Mesh."""

    vertices = []
    faces = []
    normals = []

    if type(text) is bytes:
      text = text.decode('utf8')

    for line in text.split('\n'):
      line = line.strip()
      if len(line) == 0:
        continue
      elif line[0] == '#':
        continue
      elif line[0] == 'f':
        if line.find('/') != -1:
          # e.g. f 6092/2095/6079 6087/2092/6075 6088/2097/6081
          (v1, vt1, vn1, v2, vt2, vn2, v3, vt3, vn3) = re.match(r'f\s+(\d+)/(\d*)/(\d+)\s+(\d+)/(\d*)/(\d+)\s+(\d+)/(\d*)/(\d+)', line).groups()
        else:
          (v1, v2, v3) = re.match(r'f\s+(\d+)\s+(\d+)\s+(\d+)', line).groups()
        faces.append( (int(v1), int(v2), int(v3)) )
      elif line[0] == 'v':
        if line[1] == 't': # vertex textures not supported
          # e.g. vt 0.351192 0.337058
          continue 
        elif line[1] == 'n': # vertex normals
          # e.g. vn 0.992266 -0.033290 -0.119585
          (n1, n2, n3) = re.match(r'vn\s+([-\d\.]+)\s+([-\d\.]+)\s+([-\d\.]+)', line).groups()
          normals.append( (float(n1), float(n2), float(n3)) )
        else:
          # e.g. v -0.317868 -0.000526 -0.251834
          (v1, v2, v3) = re.match(r'v\s+([-\d\.]+)\s+([-\d\.]+)\s+([-\d\.]+)', line).groups()
          vertices.append( (float(v1), float(v2), float(v3)) )

    vertices = np.array(vertices, dtype=np.float32)
    faces = np.array(faces, dtype=np.uint32)
    normals = np.array(normals, dtype=np.float32)

    return Mesh(vertices, faces - 1, normals)

  def to_obj(self):
    """Return a string representing a .obj file."""
    objdata = []
    objdata += [ 'v {:.5f} {:.5f} {:.5f}'.format(*vertex) for vertex in self.vertices ]
    objdata += [ 'f {} {} {}'.format(*face) for face in (self.faces+1) ] # obj is 1 indexed
    objdata = '\n'.join(objdata) + '\n'
    return objdata.encode('utf8')

  def to_ply(self):
    """
    Return a bytearray in .ply format, 
    a more compact format than .obj that's still widely readable.
    """
    vertexct = self.vertices.shape[0]
    trianglect = self.faces.shape[0]

    # Header
    plydata = bytearray("""ply
format binary_little_endian 1.0
element vertex {}
property float x
property float y
property float z
element face {}
property list int int vertex_indices
end_header
""".format(vertexct, trianglect).encode('utf8'))

    # Vertex data (x y z): "fff" 
    plydata.extend(self.vertices.tobytes('C'))

    # Faces (3 f1 f2 f3): "3iii" 
    plydata.extend(
      np.insert(self.faces, 0, 3, axis=1).tobytes('C')
    )

    return plydata

  @classmethod
  def from_draco(cls, binary):
    import DracoPy

    try:
      mesh_object = DracoPy.decode_buffer_to_mesh(binary)
      vertices = np.array(mesh_object.points).astype(np.float32)
      faces = np.array(mesh_object.faces).astype(np.uint32)
    except ValueError:
      raise MeshDecodeError("Not a valid draco mesh.")

    Nv = len(vertices)
    Nf = len(faces)

    if Nv % 3 != 0: 
      raise MeshDecodeError("Mesh vertices not 3D. Cannot decode. Num. vertices: {}".format(Nv))

    if Nf % 3 != 0:
      raise MeshDecodeError("Faces are not sets of triples. Cannot decode. Num. faces: {}".format(Nf))

    vertices = vertices.reshape(Nv // 3, 3)
    faces = faces.reshape(Nf // 3, 3)

    return Mesh(
      vertices, faces, normals=None,
      encoding_type='draco', 
      encoding_options=mesh_object.encoding_options
    )

  def deduplicate_chunk_boundaries(self, chunk_size, is_draco=False, draco_grid_size=21):
    # find all vertices that are exactly on chunk_size boundaries
    if is_draco:
      is_chunk_aligned = is_draco_chunk_aligned(self.vertices, chunk_size, draco_grid_size=draco_grid_size)
    else:
      is_chunk_aligned = np.any(np.mod(self.vertices, chunk_size) == 0, axis=1)

    verts, faces = self.vertices, self.faces

    # find all vertices that have exactly 2 duplicates
    unique_vertices, unique_inverse, counts = np.unique(
      verts, return_inverse=True, return_counts=True, axis=0
    )
    
    only_double = np.where(counts == 2)[0]
    is_doubled = np.isin(unique_inverse, only_double)
    # this stores whether each vertex should be merged or not
    do_merge = np.array(is_doubled & is_chunk_aligned)

    # setup an artificial 4th coordinate for vertex positions
    # which will be unique in general, 
    # but then repeated for those that are merged
    new_vertices = np.hstack((verts, np.arange(verts.shape[0])[:, np.newaxis]))
    new_vertices[do_merge, 3] = -1
  
    faces = faces.flatten()

    # use unique to make the artificial vertex list unique and reindex faces
    vertices, newfaces = np.unique(new_vertices[faces], return_inverse=True, axis=0)
    newfaces = newfaces.astype(np.uint32).reshape( (len(newfaces) // 3, 3) )

    return Mesh(vertices[:,0:3], newfaces, None, segid=self.segid, 
      encoding_type=self.encoding_type, encoding_options=self.encoding_options
    )

  def viewer(self):
    try:
      import vtk
    except ImportError:
      print("The mesh viewer requires the OpenGL based vtk. Try: pip install vtk --upgrade")
      return

    colors = vtk.vtkNamedColors()
    # Set the background color.
    bkg = map(lambda x: x / 255.0, [40, 40, 40, 255])
    colors.SetColor("BkgColor", *bkg)

    # This creates a point cloud model
    points = vtk.vtkPoints()
    verts = vtk.vtkCellArray()
    polydata = vtk.vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetVerts(verts)
    for vertex in self.vertices:
      pid = points.InsertNextPoint(vertex)
      verts.InsertNextCell(1)
      verts.InsertCellPoint(pid)

    # The mapper is responsible for pushing the geometry into the graphics
    # library. It may also do color mapping, if scalars or other
    # attributes are defined.
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    # The actor is a grouping mechanism: besides the geometry (mapper), it
    # also has a property, transformation matrix, and/or texture map.
    # Here we set its color and rotate it -22.5 degrees.
    cylinderActor = vtk.vtkActor()
    cylinderActor.SetMapper(mapper)
    cylinderActor.GetProperty().SetColor(colors.GetColor3d("Mint"))
    cylinderActor.RotateX(30.0)
    cylinderActor.RotateY(-45.0)

    # Create the graphics structure. The renderer renders into the render
    # window. The render window interactor captures mouse events and will
    # perform appropriate camera or actor manipulation depending on the
    # nature of the events.
    ren = vtk.vtkRenderer()
    renWin = vtk.vtkRenderWindow()
    renWin.AddRenderer(ren)
    iren = vtk.vtkRenderWindowInteractor()
    iren.SetRenderWindow(renWin)

    # Add the actors to the renderer, set the background and size
    ren.AddActor(cylinderActor)
    ren.SetBackground(colors.GetColor3d("BkgColor"))
    renWin.SetSize(800, 800)

    text = "Point Cloud Rendering of Mesh Object"
    if self.segid is not None:
      renWin.SetWindowName(text + " (Label {})".format(self.segid))
    else:
      renWin.SetWindowName(text)

    # This allows the interactor to initalize itself. It has to be
    # called before an event loop.
    iren.Initialize()

    # We'll zoom in a little by accessing the camera and invoking a "Zoom"
    # method on it.
    ren.ResetCamera()
    ren.GetActiveCamera().Zoom(1.5)
    renWin.Render()

    # Start the event loop.
    iren.Start()    
