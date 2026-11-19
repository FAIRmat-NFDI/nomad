#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


# Evaluation of EQE measurement data + Urbach tail to determine the radiative open-circuit voltage.
# Building from the work of Lisa Krückemeier et al. (https://doi.org/10.1002/aenm.201902573)
# Initially translated to Python by Christian Wolff


import os

import numpy as np
import pandas as pd
from scipy import integrate, optimize
from scipy.signal import savgol_filter

# Constants
temperature = 300  # in [°K]
q = 1.602176462e-19  # % [As], elementary charge
h_Js = 6.62606876e-34  # % [Js], Planck's constant
k = 1.38064852e-23  # % [(m^2)kg(s^-2)(K^-1)], Boltzmann constant
T = temperature
VT = (k * T) / q  # % [V], 25.8mV thermal voltage at 300K
c = 299792458  # % [m/s], speed of light c_0
hc_eVnm = (
    h_Js * c / q * 1e9
)  # % [eV nm]  Planck's constant for energy to wavelength conversion


class EQEAnalyzer:
    """
    A class for analyzing the EQE data of solar cells. Contains the following methods:
    - `read_file`: reads the file and returns the columns in a pandas DataFrame `df`.
    - `arrange_eqe_columns`: gets a df with columns of the file and returns a `photon_energy_raw` array and `eqe_raw` array with values of the photon energy values in *eV* and the eqe (values between 0 and 1) respectively.
    - `interpolate_eqe`: interpolates the eqe data to a given photon energy range.
    - `fit_urbach_tail`: fits the Urbach tail to the eqe data.
    - `extrapolate_eqe`: extrapolates the eqe data after having fitted an Urbach tail.
    - `calculate_jsc`: calculates the short circuit current density integrating the product
    of the eqe and the solar spectrum am1.5.
    - `calculate_voc_rad`: calculates the open circuit voltage at the radiative limit
    with the calculated `j_sc` and `j0rad`.
    """

    def __init__(self, file_path: str, header_lines=None):
        """ """
        self.file_path = file_path
        self.header_lines = header_lines

    def read_file(self):
        """
        Reads the file and returns the columns in a pandas DataFrame `df`.
        :return: df
        :rtype: pandas.DataFrame
        """
        if self.header_lines is None:
            self.header_lines = 0
        if self.header_lines == 0:  # in case you have a header
            try:
                df = pd.read_csv(
                    self.file_path,
                    header=None,
                    sep='\t',
                )
                if len(df.columns) < 2:
                    raise IndexError
            except IndexError:
                df = pd.read_csv(self.file_path, header=None)
        else:
            try:
                df = pd.read_csv(
                    self.file_path, header=int(self.header_lines - 1), sep='\t'
                )  # header_lines - 1 assumes last header line is column names
                if len(df.columns) < 2:
                    raise IndexError
            except IndexError:
                try:  # wrong separator?
                    df = pd.read_csv(self.file_path, header=int(self.header_lines - 1))
                    if len(df.columns) < 2:
                        raise IndexError
                except IndexError:
                    try:  # separator was right, but last header_line is not actually column names?
                        df = pd.read_csv(
                            self.file_path, header=int(self.header_lines), sep='\t'
                        )
                        if len(df.columns) < 2:
                            raise IndexError
                    except IndexError:
                        # Last guess: separator was wrong AND last header_line is not actually column names?
                        df = pd.read_csv(self.file_path, header=int(self.header_lines))
                        if len(df.columns) < 2:
                            raise IndexError
        df = df.apply(pd.to_numeric, errors='coerce')
        df = df.dropna()
        return df

    def arrange_eqe_columns(self):
        """
        Gets a df with columns of the file and returns a `photon_energy_raw` array
        and `eqe_raw` array with values of the photon energy values in *eV* and
        the eqe (values between 0 and 1) respectively.
        It finds if the eqe data comes in nm or eV and converts it to eV.

        Returns:
            photon_energy_raw: array of photon energy values in eV
            eqe_raw: array of eqe values
        """
        df = self.read_file()
        if 'Calculated' in list(df.columns):  # for files from the hzb
            x = df.iloc[:, 0].values
            y = df['Calculated'].values
        else:
            x = df.iloc[:, 0].values
            y = df.iloc[:, 1].values

        if any(x > 10):  # check if energy (eV) or wavelength (nm)
            x = hc_eVnm / x
        if any(
            y > 10
        ):  # check if EQE is given in (%), if so it's translated to abs. numbers
            y = y / 100
        if (
            x[1] - x[2] > 0
        ):  # bring both arrays into correct order (i.e. w.r.t eV increasing) if one started with e.g. wavelength in increasing order e.g. 300nm, 305nm,...
            x.sort()
            y = np.flip(y)

        photon_energy_raw = x
        eqe_raw = y
        return photon_energy_raw, eqe_raw

    def find_nearest(self, array, value):
        array = np.asarray(array)
        idx = (np.abs(array - value)).argmin()
        return array[idx]

    def interpolate_eqe(self):
        x, y = self.arrange_eqe_columns()
        photon_energy_interpolated = np.linspace(min(x), max(x), 1000, endpoint=True)
        eqe_interpolated = np.interp(photon_energy_interpolated, x, y)

        return photon_energy_interpolated, eqe_interpolated

    def linear(self, x, a, b):
        return a * x + b

    # Select a range from a numpy array from a given value to a given value and rerturn the indexes
    def select_range(self, array, value_start, value_end):
        idx_start = np.where(array == value_start)[0][0]
        idx_end = np.where(array == value_end)[0][0]
        return idx_start, idx_end

    # Function for linear fit of EQE data.
    def fit_urbach_tail(self, fit_window=0.06, filter_window=20):
        """
        Fits the Urbach tail to the EQE data. To select the fitting range,
        finds the maximum of the derivative of the log(eqe) data. Then selects the range
        by going down a factor of 8 in eqe values from this reference point and up a factor of 2.
        This is unfortunately only a quick fix, but it works well enough based a few empirical tests
        with eqe data of perovskite solar cells.

        Returns:
            urbach_e: urnach energy in eV
            m:
            fit_min: photon energy of the minimum of the fitted range
            fit_max: photon energy of the maximum of the fitted range
        """
        x, y = self.interpolate_eqe()
        y = savgol_filter(
            y, 51, 4, mode='mirror'
        )  # apply Savitzky-Golay filter to smooth the data
        self.data = pd.DataFrame({'y': y})
        log_data = self.data.apply(np.log)
        # find inflection point
        infl_point = (
            log_data.rolling(
                window=filter_window, min_periods=int(filter_window / 4), center=True
            )
            .mean()
            .diff()
            .idxmax()
        )
        min_eqe_fit = self.find_nearest(y, y[infl_point] / 8)
        max_eqe_fit = self.find_nearest(y, y[infl_point] * 2)
        # min_eqe_fit = self.find_nearest(y, min(y[0:20] * 2))
        self.min_eqe_fit = min_eqe_fit
        # max_eqe_fit = self.find_nearest(y, min_eqe_fit * 8)
        self.max_eqe_fit = max_eqe_fit
        start, stop = self.select_range(y, min_eqe_fit, max_eqe_fit)
        self.start = start
        self.stop = stop
        popt, pcov = optimize.curve_fit(  # pylint: disable=unbalanced-tuple-unpacking
            self.linear, x[start:stop], np.log(y[start:stop]), p0=[min(y) * 8, 0.026]
        )
        m = popt[1]
        fit_min, fit_max = x[start], x[stop]
        urbach_e = 1 / popt[0]
        # calculate the standard dev of popt[0]
        perr = np.sqrt(np.diag(pcov))
        urbach_e_std = perr[0] / (popt[0] ** 2)

        # print('Voc rad: ' + str(voc_rad) + ' V')
        # if urbach_e <= 0.0:
        #     raise ValueError('''Failed to estimate a reasonable Urbach Energy.''')

        return urbach_e, m, fit_min, fit_max, urbach_e_std

    # Extrapolate with an array of the fitted fitted EQE data to the interpolated eqe at a value of min_eqe_fit
    def extrapolate_eqe(self):
        """
        Extrapolates the EQE data with the fitted Urbach tail.

        Returns:
            photon_energy_extrapolated: array of the extrapolated photon energy values in eV
            eqe_extrapolated: array of the extrapolated eqe values
        """
        try:
            x, y = self.interpolate_eqe()
            urbach_e = self.fit_urbach_tail()[0]
            min_eqe_fit = self.min_eqe_fit
            x_extrap = np.linspace(-1, 0, 500, endpoint=False) + x[self.stop]
            y_extrap = y[self.stop] * np.exp((x_extrap - x[self.stop]) * 1 / urbach_e)
            x_interp = np.linspace(x[self.stop], max(x), 1000, endpoint=True)
            y_interp = np.interp(
                x_interp,
                x[max(self.start, self.stop) :],
                y[max(self.start, self.stop) :],
            )
            x_interp = x_interp[y_interp >= min_eqe_fit]
            y_interp = y_interp[y_interp >= min_eqe_fit]
            x_extrap = np.linspace(-1, 0, 500, endpoint=False) + min(x_interp)
            y_extrap = y_interp[0] * np.exp((x_extrap - min(x_interp)) / urbach_e)
            photon_energy_extrapolated = np.append(x_extrap, x_interp)
            eqe_extrapolated = np.append(y_extrap, y_interp)
        except ValueError:
            print("""The eqe could not be extrapolated because it was not possible
            to estimate the Urbach energy.""")
        return photon_energy_extrapolated, eqe_extrapolated

    def calculate_jsc(self):
        """
        Calculates the short circuit current (jsc) from the extrapolated eqe.

        Returns:
            jsc: short circuit current density in A m**(-2)
        """
        x, y = self.interpolate_eqe()
        dir_path = os.path.dirname(os.path.realpath(__file__))
        filename2 = os.path.join(dir_path, 'AM15G.dat.txt')
        df_am15 = pd.read_csv(filename2, header=None)
        energy_AM15 = np.array(df_am15[df_am15.columns[1]])
        spectrum_AM15 = np.array(df_am15[df_am15.columns[2]])
        spectrum_AM15G_interp = np.interp(x, energy_AM15, spectrum_AM15)
        jsc_calc = integrate.cumulative_trapezoid(y * spectrum_AM15G_interp, x)
        jsc = max(jsc_calc * q * 1e4)
        return jsc

    # Calculates the bandgap from the inflection point of the eqe.
    def calculate_bandgap(self):
        """
        calculates the bandgap from the inflection point of the eqe.

        Returns:
            bandgap: bandgap in eV calculated from in the inflection point of the eqe
        """
        x, y = self.interpolate_eqe()
        y = savgol_filter(y, 51, 4, mode='nearest')
        deqe_interp = np.diff(y) / np.diff(np.flip(-x))
        bandgap = x[deqe_interp.argmax()]
        # print('Bandgap: ' + str(bandgap) + ' eV')
        return bandgap

    def calculate_j0rad(self):
        """
        Calculates the radiative saturation current (j0rad) and the calculated electroluminescence (EL)
        spectrum (Rau's reciprocity) from the extrapolated eqe.

        Returns:
            j0rad: radiative saturation current density in A m**(-2)
            EL: EL spectrum
        """
        try:
            urbach_e = self.fit_urbach_tail()[0]
            # try to calculate the j0rad and EL spectrum except if the urbach energy is larger than 0.026
            if urbach_e >= 0.026 or urbach_e <= 0.0:
                raise ValueError("""Urbach energy is > 0.026 eV (~kB*T for T = 300K), or
                it could notbe estimated. The `j0rad` could not be calculated.""")

            x, y = self.extrapolate_eqe()
            phi_BB = (2 * np.pi * q**3 * (x) ** 2) / (
                h_Js**3 * c**2 * (np.exp(x / VT) - 1)
            )
            el = phi_BB * y
            if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
                j0rad = np.trapezoid(el, x)
            else:
                j0rad = np.trapz(el, x)  # noqa: NPY201
            j0rad = j0rad * q
        except ValueError:
            raise ValueError("""Failed to estimate a reasonable Urbach Energy.""")
        # print('Radiative saturation current: ' + str(j0rad) + ' A / m^2')
        return j0rad, el

    def calculate_voc_rad(self):
        """
        Calculates the radiative open circuit voltage (voc_rad) with the calculated j0rad
        and j_sc.

        Returns:
            voc_rad: radiative open circuit voltage in V
        """
        try:
            j0rad = self.calculate_j0rad()[0]
            jsc = self.calculate_jsc()
            voc_rad = VT * np.log(jsc / j0rad)
            # print('Voc rad: ' + str(voc_rad) + ' V')
        except ValueError:
            raise ValueError("""Urbach energy is > 0.026 eV (~kB*T for T = 300K).
                           The `j0rad` could not be calculated.""")
        return voc_rad

    def plot_eqe(self):
        """
        Plots the extrapolated eqe ad the raw eqe.
        """
        import matplotlib.pyplot as plt

        x, y = self.arrange_eqe_columns()
        photon_energy_extrapolated, eqe_extrapolated = self.extrapolate_eqe()
        bandgap = self.calculate_bandgap()
        fit_min, fit_max = self.fit_urbach_tail()[2], self.fit_urbach_tail()[3]
        # plot in log scale the extrapolated eqe
        plt.rcParams.update({'font.size': 16, 'font.family': 'Arial'})
        plt.plot(photon_energy_extrapolated, eqe_extrapolated, label='extrapolated EQE')
        plt.ylim(1e-4, 1.1)
        plt.xlim(bandgap - 0.2, bandgap + 0.2)
        plt.yscale('log')
        # Scatter plot the interpolated eqe in red marker with label raw data.
        plt.scatter(x, y, color='red', alpha=0.4, label='raw data')
        plt.xlabel('Photon energy (eV)')
        plt.ylabel('EQE')
        plt.axvline(x=fit_min, color='black', linestyle='--')
        plt.axvline(x=fit_max, color='black', linestyle='--')
        plt.legend()
        plt.show()

    def plot_eqe_raw(self):
        import matplotlib.pyplot as plt

        x, y = self.arrange_eqe_columns()
        plt.rcParams.update({'font.size': 16, 'font.family': 'Arial'})
        plt.ylim(1e-4, 1.1)
        plt.yscale('log')
        plt.scatter(x, y, color='red', alpha=0.4, label='raw data')
        plt.xlabel('Photon energy (eV)')
        plt.ylabel('EQE')
        plt.legend()
        plt.show()

    def eqe_dict(self):
        eqe_dict = {}
        eqe_dict['photon_energy_raw'], eqe_dict['eqe_raw'] = self.arrange_eqe_columns()
        eqe_dict['interpolated_photon_energy'], eqe_dict['interpolated_eqe'] = (
            self.interpolate_eqe()
        )
        eqe_dict['jsc'] = self.calculate_jsc()
        eqe_dict['bandgap'] = self.calculate_bandgap()
        if self.fit_urbach_tail()[0] <= 0.0 or self.fit_urbach_tail()[0] >= 0.5:
            print('Failed to estimate a reasonable Urbach Energy')
        else:
            eqe_dict['urbach_e'] = self.fit_urbach_tail()[0]
            eqe_dict['error_urbach_std'] = self.fit_urbach_tail()[4]
            eqe_dict['photon_energy_extrapolated'], eqe_dict['eqe_extrapolated'] = (
                self.extrapolate_eqe()
            )
        try:
            eqe_dict['j0rad'], eqe_dict['el'] = self.calculate_j0rad()
            eqe_dict['voc_rad'] = self.calculate_voc_rad()
        except ValueError:
            print(
                'Urbach energy is > 0.026 eV (~kB*T for T = 300K).\n'
                'The `j0rad`, `el` and `voc_rad` could not be calculated.'
            )

        return eqe_dict
