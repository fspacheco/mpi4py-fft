import warnings
import six
import numpy as np
from mpi4py import MPI
from .file_base import FileBase

# https://github.com/Unidata/netcdf4-python/blob/master/examples/mpi_example.py

try:
    from netCDF4 import Dataset
except ImportError: #pragma: no cover
    warnings.warn('netCDF4 not installed')

__all__ = ('NCFile',)

comm = MPI.COMM_WORLD

class NCFile(FileBase):
    """Class for writing data to NetCDF4 format

    Parameters
    ----------
        ncname : str
            Name of netcdf file to be created
        T : PFFT
            Instance of a :class:`.PFFT` class. Must be the same as the space
            used for storing with :class:`NCWriter.write`
        domain : Sequence
            The spatial domain. Sequence of either

                - 2-tuples, where each 2-tuple contains the (origin, length)
                  of each dimension, e.g., (0, 2*pi).
                - Arrays of coordinates, e.g., np.linspace(0, 2*pi, N). One
                  array per dimension.

        clobber : bool, optional
        kw : dict
            Additional keywords

    Note
    ----
    Each class instance creates one unique NetCDF4-file, with one step-counter.
    It is possible to store multiple fields in each file, but all snapshots of
    the fields must be taken at the same time. If you want one field stored
    every 10th timestep and another every 20th timestep, then use two different
    class instances and as such two NetCDF4-files.
    """
    def __init__(self, ncname, T, domain=None, clobber=True, mode='w', **kw):
        FileBase.__init__(self, T, domain=domain)
        self.f = Dataset(ncname, mode=mode, clobber=clobber, parallel=True, comm=comm, **kw)
        self.N = N = T.shape(False)
        dtype = self.T.dtype(False)
        assert dtype.char in 'fdg'
        self._dtype = dtype

        if mode == 'w':
            self.f.createDimension('time', None)
            self.dims = ['time']
            self.nc_t = self.f.createVariable('time', self._dtype, ('time'))
            self.nc_t.set_collective(True)

            d = list(domain)
            if not isinstance(domain[0], np.ndarray):
                assert len(domain[0]) == 2
                for i in range(len(domain)):
                    d[i] = np.arange(N[i], dtype=np.float)*2*np.pi/N[i]

            for i in range(len(d)):
                xyz = {0:'x', 1:'y', 2:'z'}[i]
                self.f.createDimension(xyz, N[i])
                self.dims.append(xyz)
                nc_xyz = self.f.createVariable(xyz, self._dtype, (xyz))
                nc_xyz[:] = d[i]

            self.handles = dict()
            self.f.sync()

    def write(self, step, fields, **kw):
        """Write snapshot step of ``fields`` to NetCDF4 file

        Parameters
        ----------
        step : int
            Index of snapshot
        fields : dict
            The fields to be dumped to file. (key, value) pairs are group name
            and either arrays or 2-tuples, respectively. The arrays are complete
            arrays to be stored, whereas 2-tuples are arrays with associated
            *global* slices.
        kw : dict
            Additional keywords for overloading

        FIXME: NetCDF4 hangs in parallel for slices if some of the
        processors do not contain the slice.

        """
        it = self.nc_t.size
        self.nc_t[it] = step
        FileBase.write(self, it, fields)

    def read(self, u, dset, step=0, **kw):
        """Read into array ``u``

        Parameters
        ----------
        u : array
            The array to read into
        dset : str
            Name of array to be read
        step : int, optional
            Index of field to be read
        kw : dict
            Additional keywords
        """
        s = self.T.local_slice(False)
        s = [step] + s
        u[:] = self.f[dset][tuple(s)]

    def _write_group(self, name, u, it, **kw):
        s = self.T.local_slice(False)
        if name not in self.handles:
            self.handles[name] = self.f.createVariable(name, self._dtype, self.dims)
            self.handles[name].set_collective(True)
        if self.T.ndim() == 3:
            self.handles[name][it, s[0], s[1], s[2]] = u
        elif self.T.ndim() == 2:
            self.handles[name][it, s[0], s[1]] = u
        else:
            raise NotImplementedError
        self.f.sync()

    def _write_slice_step(self, name, it, slices, u, **kw):
        slices = list(slices)
        slname = self._get_slice_name(slices)
        s = self.T.local_slice(False)

        slices, inside = self._get_local_slices(slices, s)
        sp = np.nonzero([isinstance(x, slice) for x in slices])[0]
        sf = np.take(s, sp)
        sdims = ['time'] + list(np.take(self.dims, np.array(sp)+1))
        fname = "_".join((name, slname))

        if fname not in self.handles:
            self.handles[fname] = self.f.createVariable(fname, self._dtype, sdims)
            self.handles[fname].set_collective(True)
            self.handles[fname].setncattr_string('slices', str(slices))

        sl = tuple(slices)
        if inside:
            if len(sf) == 3: #pragma: no cover
                self.handles[fname][it, sf[0], sf[1], sf[2]] = u[sl]
            elif len(sf) == 2:
                self.handles[fname][it, sf[0], sf[1]] = u[sl]
            elif len(sf) == 1:
                self.handles[fname][it, sf[0]] = u[sl]

        self.f.sync()
