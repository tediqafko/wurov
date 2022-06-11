#!/usr/bin/env python3

import time
import argparse
import board;
import busio;
import adafruit_fxas21002c;
import adafruit_fxos8700;
import rospy
from sensor_msgs.msg import Imu, MagneticField
from ddynamic_reconfigure_python.ddynamic_reconfigure import DDynamicReconfigure

class ImuCalibration:
    def __init__(self) -> None:
        parser = argparse.ArgumentParser("IMU Calibration")

        # NOTE: Only recalibrate IMU when the IMU is oriented correctly and when the vehicle is on a flat surface
        parser.add_argument('--accel_calibration', type=bool, help='set to true if calibrating Accelerometer')
        parser.add_argument('--gyro_calibration', type=bool, help='set to true if calibrating Gyroscope')
        parser.add_argument('--mag_calibration', type=bool, help='set to true if calibrating Magnetometer')
        parser.add_argument('--dynamic_calibration', type=bool, help='set to true if dynamically calibrating the IMU')

        self.args = parser.parse_args(rospy.myargv()[1:])

        # init hardwares
        i2c             = busio.I2C(board.SCL, board.SDA)
        self.gyroSensor = adafruit_fxas21002c.FXAS21002C(i2c)
        self.sensor     = adafruit_fxos8700.FXOS8700(i2c)

        # imu_filter_madgwick input topics
        rospy.init_node('imu_calibration', anonymous=True)
        self.imu_raw_pub    = rospy.Publisher('imu/data_no_offsets', Imu, queue_size=3)
        self.imu_offset_pub = rospy.Publisher('imu/data_with_offsets', Imu, queue_size=3)
        self.mag_raw_pub    = rospy.Publisher('imu/mag_no_offsets', MagneticField, queue_size=3)
        self.mag_offset_pub = rospy.Publisher('imu/mag_with_offsets', MagneticField, queue_size=3)

        # init messages
        self.imu_msg            = Imu()
        self.imu_offset_msg     = Imu()
        self.mag_msg            = MagneticField()
        self.mag_offset_msg     = MagneticField()

        # get offset from rosparam server
        lao = rospy.get_param("linear_accel_offset")
        avo = rospy.get_param("angular_vel_offset")
        mfo = rospy.get_param("magnetic_field_offset")

        # init offset values
        self.linear_accel_offset    = {'x': lao[0], 'y': lao[1], 'z': lao[2]}
        self.angular_vel_offset     = {'x': avo[0], 'y': avo[1], 'z': avo[2]}
        self.magnetic_field_offset  = {'x': mfo[0], 'y': mfo[1], 'z': mfo[2]}

        rospy.Timer(rospy.Duration(0.1), self.pubish_imu_raw)
        rospy.Timer(rospy.Duration(0.1), self.pubish_mag_raw)

        rospy.Subscriber('imu/data_no_offsets', Imu, self.pubish_imu_offset)
        rospy.Subscriber('imu/mag_no_offsets', MagneticField, self.pubish_mag_offset)

        rospy.spin()

    def pubish_imu_raw(self, data):
        # REP103:
        # +x: forward
        # +y: left
        # +z: up

        # Sensor readings
        accel_y, accel_z, accel_x   = self.sensor.accelerometer                   # in m/s^2
        ang_y, ang_z, ang_x         = self.gyroSensor.gyroscope                   # in Radians/s
        # Populate IMU message
        self.imu_msg.header.stamp            = rospy.Time.now()
        self.imu_msg.header.frame_id         = 'base_link'
        self.imu_msg.linear_acceleration.x   = accel_x
        self.imu_msg.linear_acceleration.y   = accel_y
        self.imu_msg.linear_acceleration.z   = -accel_z
        self.imu_msg.angular_velocity.x      = ang_x 
        self.imu_msg.angular_velocity.y      = ang_y 
        self.imu_msg.angular_velocity.z      = ang_z
        # publish msgs
        self.imu_raw_pub.publish(self.imu_msg)

    def pubish_mag_raw(self, data):
        # Sensor readings
        mag_y, mag_z, mag_x = [k/1000000 for k in self.sensor.magnetometer]     # in Tesla
        # Populate Mag message
        self.mag_msg.header.stamp       = rospy.Time.now()
        self.mag_msg.header.frame_id    = 'base_link'
        self.mag_msg.magnetic_field.x   = mag_x
        self.mag_msg.magnetic_field.y   = mag_y
        self.mag_msg.magnetic_field.z   = mag_z
        self.mag_raw_pub.publish(self.mag_msg)

    def pubish_mag_offset(self, data: MagneticField):
        self.mag_offset_msg.header.stamp       = data.header.stamp
        self.mag_offset_msg.header.frame_id    = data.header.frame_id
        self.mag_offset_msg.magnetic_field.x   = data.magnetic_field.x - self.magnetic_field_offset['x']
        self.mag_offset_msg.magnetic_field.y   = data.magnetic_field.y - self.magnetic_field_offset['y']
        self.mag_offset_msg.magnetic_field.z   = data.magnetic_field.z - self.magnetic_field_offset['z']
        self.mag_offset_pub.publish(self.mag_offset_msg)

    def pubish_imu_offset(self, data: Imu):
        self.imu_offset_msg.header.stamp           = data.header.stamp
        self.imu_offset_msg.header.frame_id        = data.header.frame_id
        self.imu_offset_msg.linear_acceleration.x  = data.linear_acceleration.x - self.linear_accel_offset['x']
        self.imu_offset_msg.linear_acceleration.y  = data.linear_acceleration.y - self.linear_accel_offset['y']
        self.imu_offset_msg.linear_acceleration.z  = -abs(data.linear_acceleration.z  - self.linear_accel_offset['z'])     # negative absolute is to ensure z-axis is always -9.8 m/s initally
        self.imu_offset_msg.angular_velocity.x     = data.angular_velocity.x - self.angular_vel_offset['x']
        self.imu_offset_msg.angular_velocity.y     = data.angular_velocity.y - self.angular_vel_offset['y']
        self.imu_offset_msg.angular_velocity.z     = data.angular_velocity.z - self.angular_vel_offset['z']
        self.imu_offset_pub.publish(self.imu_offset_msg)

    #TODO: refactor this
    def calculate_accel_offset(self, duration=2, sampling_rate=10):
        duration = duration + time.time()
        period   = 1/sampling_rate
        x = []
        y = []
        z = []
        while time.time() < duration:
            accel_y, accel_z, accel_x = self.sensor.accelerometer                   # in m/s^2
            x.append(accel_x)
            y.append(accel_y)
            z.append(accel_z)
            time.sleep(period)
        self.linear_accel_offset['x'] = sum(x)/len(x)
        self.linear_accel_offset['y'] = sum(y)/len(y)
        self.linear_accel_offset['z'] = sum(z)/len(z) - 9.8
