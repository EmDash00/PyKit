from constants import kRobotPeriod
from subsystems.roller import rollerconstants
from subsystems.roller.rollerio import RollerIO
from util.helpfulmath import clamp
from wpilib.simulation import DCMotorSim
from wpimath.system.plant import DCMotor, LinearSystemId


class RollerIOSim(RollerIO):
    def __init__(self) -> None:
        self.appliedVolts = 0.0
        self.sim = DCMotorSim(
            LinearSystemId.DCMotorSystem(
                DCMotor.CIM(1), 0.004, rollerconstants.kMotorReduction
            ),
            DCMotor.CIM(1),
        )

    def updateInputs(self, inputs: RollerIO.RollerIOInputs) -> None:
        self.sim.setInputVoltage(self.appliedVolts)
        self.sim.update(kRobotPeriod)

        inputs.positionRad = self.sim.getAngularPosition()
        inputs.velocityRadPerSec = self.sim.getAngularVelocity()
        inputs.appliedVolts = self.appliedVolts
        inputs.currentAmps = self.sim.getCurrentDraw()

    def setVoltage(self, volts: float) -> None:
        self.appliedVolts = clamp(volts, -12.0, 12.0)
