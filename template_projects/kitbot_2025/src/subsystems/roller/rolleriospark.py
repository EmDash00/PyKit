from constants.math import kRadiansPerRevolution, kSecondsPerMinute
from rev import SparkMax, SparkMaxConfig
from subsystems.roller import rollerconstants
from subsystems.roller.rollerio import RollerIO
from util.sparkutil import isOk, tryUntilOk


class RollerIOSpark(RollerIO):
    def __init__(self) -> None:
        self.rollerMotor = SparkMax(
            rollerconstants.kRollerCanId, SparkMax.MotorType.kBrushless
        )
        self.encoder = self.rollerMotor.getEncoder()

        config = SparkMaxConfig()
        config.setIdleMode(SparkMaxConfig.IdleMode.kBrake).smartCurrentLimit(
            rollerconstants.kCurrentLimit
        ).voltageCompensation(12.0)
        config.encoder.positionConversionFactor(
            kRadiansPerRevolution / rollerconstants.kMotorReduction
        ).velocityConversionFactor(
            kRadiansPerRevolution / kSecondsPerMinute / rollerconstants.kMotorReduction
        ).uvwMeasurementPeriod(
            10
        ).uvwAverageDepth(
            2
        )

        tryUntilOk(
            5,
            lambda: self.rollerMotor.configure(
                config,
                SparkMax.ResetMode.kResetSafeParameters,
                SparkMax.PersistMode.kPersistParameters,
            ),
        )

    def updateInputs(self, inputs: RollerIO.RollerIOInputs) -> None:
        isOk(
            self.rollerMotor,
            self.encoder.getPosition,
            lambda v: setattr(inputs, "positionRad", v),
        )
        isOk(
            self.rollerMotor,
            self.encoder.getVelocity,
            lambda v: setattr(inputs, "velocityRadPerSec", v),
        )
        isOk(
            self.rollerMotor,
            self.rollerMotor.getAppliedOutput,
            lambda v: setattr(inputs, "appliedVolts", v * 12.0),
        )
        isOk(
            self.rollerMotor,
            self.rollerMotor.getOutputCurrent,
            lambda v: setattr(inputs, "currentAmps", v),
        )

    def setVoltage(self, volts: float) -> None:
        pass
