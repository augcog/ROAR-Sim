# References:
# https://github.com/asap-report/carla/blob/racetrack/PythonClient/racetrack/model_predictive_control.py

""" This module contains MPC controller. """

import logging
import numpy as np
import pandas as pd
import sympy as sym

from pathlib import Path
from scipy.optimize import minimize
from sympy.tensor.array import derive_by_array
from roar_autonomous_system.control.controller import Controller
from roar_autonomous_system.util.models import Control, Vehicle, Transform, Location


# set up pretty print
# sym.init_printing()


class _EqualityConstraints(object):
    """Class for storing equality constraints in the MPC."""

    def __init__(self, N, state_vars):
        self.dict = {}
        for symbol in state_vars:
            self.dict[symbol] = N*[None]

    def __getitem__(self, key):
        return self.dict[key]

    def __setitem__(self, key, value):
        self.dict[key] = value


class VehicleMPCController(Controller):
    def __init__(self,
                 vehicle: Vehicle,
                 file_path: Path, # hard-code read in data for now
                 target_speed=float("inf"),
                 steps_ahead=10,
                 max_throttle=1,
                 max_steering=1,
                 dt=0.1):
        super().__init__(vehicle)
        self.logger = logging.getLogger(__name__)

        # Read in route file
        self.map = pd.read_csv(file_path, header=None)
        self.map.columns = ['x', 'y', 'z']
        self.map_2D = pd.DataFrame()
        self.map_2D['x'] = self.map['x']
        self.map_2D['y'] = self.map['y']

        self.target_speed = target_speed
        self.state_vars = ('x', 'y', 'v', 'ψ', 'cte', 'eψ')

        self.steps_ahead = steps_ahead
        self.dt = dt

        # Cost function coefficients
        self.cte_coeff = 100  # 100
        self.epsi_coeff = 100  # 100
        self.speed_coeff = 0.4  # 0.2
        self.acc_coeff = 1  # 1
        self.steer_coeff = 0.1  # 0.1
        self.consec_acc_coeff = 50
        self.consec_steer_coeff = 50

        # Front wheel L
        self.Lf = 2.5

        # How the polynomial fitting the desired curve is fitted
        self.steps_poly = 30
        self.poly_degree = 3

        # Bounds for the optimizer
        self.bounds = (
                6 * self.steps_ahead * [(None, None)]
                + self.steps_ahead * [(0, max_throttle)] # throttle bounds
                + self.steps_ahead * [(-max_steering, max_steering)] # steer bounds
        )

        # State 0 placeholder
        num_vars = (len(self.state_vars) + 2)  # State variables and two actuators
        self.state0 = np.zeros(self.steps_ahead * num_vars)

        # Lambdify and minimize stuff
        self.evaluator = 'numpy'
        self.tolerance = 1
        self.cost_func, self.cost_grad_func, self.constr_funcs = self.get_func_constraints_and_bounds()

        # To keep the previous state
        self.steer = None
        self.throttle = None

        self.logger.debug("MPC Controller initiated")
        # self.logger.debug(f"  cost_func:      {self.cost_func}")
        # self.logger.debug(f"  cost_grad_func: {self.cost_grad_func}")
        # self.logger.debug(f"  constr_funcs:   {self.constr_funcs}")

    def run_step(self, next_waypoint: Transform) -> Control:
        location = self.vehicle.transform.location   

        orient = self.vehicle.transform.rotation
        v = Vehicle.get_speed(self.vehicle)
        ψ = np.arctan2(orient.pitch, orient.roll)

        cos_ψ = np.cos(ψ)
        sin_ψ = np.sin(ψ)

        x, y = location.x, location.y

        # modified version
        pts = [next_waypoint.location.x, next_waypoint.location.y]
        pts_car = VehicleMPCController.modified_transform_into_cars_coordinate_system(pts, x, y, cos_ψ, sin_ψ)
        poly = np.polyfit(np.array([pts_car[0]]), np.array([pts_car[1]]), self.poly_degree)

        # WIP: get approx waypoints
        # which_closest, _, _ = VehicleMPCController.calculate_closest_dists_and_location(
        #     x,
        #     y,
        #     self.map_2D
        # )

        # which_closest_shifted = which_closest - 5
        # indeces = which_closest_shifted + self.steps_poly*np.arange(self.poly_degree+1)
        # indeces = indeces % self.map_2D.shape[0]
        # pts = self.map_2D.iloc[indeces]

        # pts_car = VehicleMPCController.transform_into_cars_coordinate_system(pts, x, y, cos_ψ, sin_ψ)
        # poly = np.polyfit(pts_car[:, 0], pts_car[:, 1], self.poly_degree)

        cte = poly[-1]
        eψ = -np.arctan(poly[-2])

        init = (0, 0, 0, v, cte, eψ, *poly)
        self.state0 = self.get_state0(v, cte, eψ, self.steer, self.throttle, poly)
        result = self.minimize_cost(self.bounds, self.state0, init)

        control = Control()
        if 'success' in result.message:
            control.steering = result.x[-self.steps_ahead]
            control.throttle = result.x[-2*self.steps_ahead]
        else:
            self.logger.debug('Unsuccessful optimization')

        return control

    def sync(self):
        pass

    def get_func_constraints_and_bounds(self):
        """
        Defines MPC's cost function and constraints.
        """
        # Polynomial coefficients will also be symbolic variables
        poly = self.create_array_of_symbols('poly', self.poly_degree + 1)

        # Initialize the initial state
        x_init = sym.symbols('x_init')
        y_init = sym.symbols('y_init')
        ψ_init = sym.symbols('ψ_init')
        v_init = sym.symbols('v_init')
        cte_init = sym.symbols('cte_init')
        eψ_init = sym.symbols('eψ_init')

        init = (x_init, y_init, ψ_init, v_init, cte_init, eψ_init)

        # State variables
        x = self.create_array_of_symbols('x', self.steps_ahead)
        y = self.create_array_of_symbols('y', self.steps_ahead)
        ψ = self.create_array_of_symbols('ψ', self.steps_ahead)
        v = self.create_array_of_symbols('v', self.steps_ahead)
        cte = self.create_array_of_symbols('cte', self.steps_ahead)
        eψ = self.create_array_of_symbols('eψ', self.steps_ahead)

        # Actuators
        a = self.create_array_of_symbols('a', self.steps_ahead)
        δ = self.create_array_of_symbols('δ', self.steps_ahead)

        vars_ = (
            # Symbolic arrays (but NOT actuators)
            *x, *y, *ψ, *v, *cte, *eψ,

            # Symbolic arrays (actuators)
            *a, *δ,
        )

        cost = 0
        for t in range(self.steps_ahead):
            cost += (
                # Reference state penalties
                    self.cte_coeff * cte[t] ** 2
                    + self.epsi_coeff * eψ[t] ** 2 +
                    + self.speed_coeff * (v[t] - self.target_speed) ** 2

                    # Actuator penalties
                    + self.acc_coeff * a[t] ** 2
                    + self.steer_coeff * δ[t] ** 2
            )

        # Penalty for differences in consecutive actuators
        for t in range(self.steps_ahead - 1):
            cost += (
                    self.consec_acc_coeff * (a[t + 1] - a[t]) ** 2
                    + self.consec_steer_coeff * (δ[t + 1] - δ[t]) ** 2
            )

        # Initialize constraints
        eq_constr = _EqualityConstraints(self.steps_ahead, self.state_vars)
        eq_constr['x'][0] = x[0] - x_init
        eq_constr['y'][0] = y[0] - y_init
        eq_constr['ψ'][0] = ψ[0] - ψ_init
        eq_constr['v'][0] = v[0] - v_init
        eq_constr['cte'][0] = cte[0] - cte_init
        eq_constr['eψ'][0] = eψ[0] - eψ_init

        for t in range(1, self.steps_ahead):
            curve = sum(poly[-(i+1)] * x[t-1]**i for i in range(len(poly)))
            # The desired ψ is equal to the derivative of the polynomial curve at
            #  point x[t-1]
            ψdes = sum(poly[-(i+1)] * i*x[t-1]**(i-1) for i in range(1, len(poly)))

            eq_constr['x'][t] = x[t] - (x[t-1] + v[t-1] * sym.cos(ψ[t-1]) * self.dt)
            eq_constr['y'][t] = y[t] - (y[t-1] + v[t-1] * sym.sin(ψ[t-1]) * self.dt)
            eq_constr['ψ'][t] = ψ[t] - (ψ[t-1] - v[t-1] * δ[t-1] / self.Lf * self.dt)
            eq_constr['v'][t] = v[t] - (v[t-1] + a[t-1] * self.dt)
            eq_constr['cte'][t] = cte[t] - (curve - y[t-1] + v[t-1] * sym.sin(eψ[t-1]) * self.dt)
            eq_constr['eψ'][t] = eψ[t] - (ψ[t-1] - ψdes - v[t-1] * δ[t-1] / self.Lf * self.dt)

        # Generate actual functions from
        cost_func = self.generate_fun(cost, vars_, init, poly)
        cost_grad_func = self.generate_grad(cost, vars_, init, poly)

        constr_funcs = []
        for symbol in self.state_vars:
            for t in range(self.steps_ahead):
                func = self.generate_fun(eq_constr[symbol][t], vars_, init, poly)
                grad_func = self.generate_grad(eq_constr[symbol][t], vars_, init, poly)
                constr_funcs.append(
                    {'type': 'eq', 'fun': func, 'jac': grad_func, 'args': None},
                )

        return cost_func, cost_grad_func, constr_funcs

    def generate_fun(self, symb_fun, vars_, init, poly):
        """
        Generates a function of the form `fun(x, *args)`
        """
        args = init + poly
        return sym.lambdify((vars_, *args), symb_fun, self.evaluator)

    def generate_grad(self, symb_fun, vars_, init, poly):
        """
        TODO: add comments
        """
        args = init + poly
        return sym.lambdify(
            (vars_, *args),
            derive_by_array(symb_fun, vars_ + args)[:len(vars_)],
            self.evaluator
        )

    def get_state0(self, v, cte, epsi, a, delta, poly):
        a = a or 0
        delta = delta or 0

        x = np.linspace(0, 1, self.steps_ahead)
        y = np.polyval(poly, x)
        psi = 0

        self.state0[:self.steps_ahead] = x
        self.state0[self.steps_ahead:2 * self.steps_ahead] = y
        self.state0[2 * self.steps_ahead:3 * self.steps_ahead] = psi
        self.state0[3 * self.steps_ahead:4 * self.steps_ahead] = v
        self.state0[4 * self.steps_ahead:5 * self.steps_ahead] = cte
        self.state0[5 * self.steps_ahead:6 * self.steps_ahead] = epsi
        self.state0[6 * self.steps_ahead:7 * self.steps_ahead] = a
        self.state0[7 * self.steps_ahead:8 * self.steps_ahead] = delta
        return self.state0

    def minimize_cost(self, bounds, x0, init):
        for constr_func in self.constr_funcs:
            constr_func['args'] = init

        return minimize(
            fun=self.cost_func,
            x0=x0,
            args=init,
            jac=self.cost_grad_func,
            bounds=bounds,
            constraints=self.constr_funcs,
            method='SLSQP',
            tol=self.tolerance,
        )

    @staticmethod
    def create_array_of_symbols(str_symbol, N):
        return sym.symbols('{symbol}0:{N}'.format(symbol=str_symbol, N=N))

    @staticmethod
    def calculate_closest_dists_and_location(x, y, map_2D):
        location = np.array([x, y])
        dists = np.linalg.norm(map_2D - location, axis=1)
        which_closest = np.argmin(dists)
        return which_closest, dists, location

    @staticmethod
    def transform_into_cars_coordinate_system(pts, x, y, cos_ψ, sin_ψ):
        diff = (pts - [x, y])
        pts_car = np.zeros_like(diff)
        pts_car[:, 0] = cos_ψ * diff.iloc[:, 0] + sin_ψ * diff.iloc[:, 1]
        pts_car[:, 1] = sin_ψ * diff.iloc[:, 0] - cos_ψ * diff.iloc[:, 1]
        return pts_car

    @staticmethod
    def modified_transform_into_cars_coordinate_system(pts, x, y, cos_ψ, sin_ψ):
        """Note: this func is modified to use only one waypoint
        """
        diff = (np.array(pts) - [x, y])
        pts_car = np.zeros_like(diff)
        pts_car[0] = cos_ψ * diff[0] + sin_ψ * diff[1]
        pts_car[1] = sin_ψ * diff[0] - cos_ψ * diff[1]
        return pts_car
