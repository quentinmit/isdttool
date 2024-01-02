# coding=utf-8

"""This file contains functions to connect to BLE chargers."""
import asyncio
import contextlib
from dataclasses import dataclass, field, make_dataclass
from uuid import UUID, uuid4
import logging

from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic, AdvertisementData, BLEDevice, BleakError
from bleak_retry_connector import establish_connection, retry_bluetooth_connection_error, BLEAK_RETRY_EXCEPTIONS
import construct as cs
from construct_dataclasses import dataclass_struct, csfield, subcsfield, to_struct

# Usually FW Update service
UUID_BLE_SERVICE_FEE0 = '0000fee0-0000-1000-8000-00805f9b34fb'
# Main charger data
UUID_BLE_SERVICE_AF00 = '0000af00-0000-1000-8000-00805f9b34fb'
UUID_BLE_SERVICE_DB00 = '0000db00-0000-1000-8000-00805f9b34fb'

UUID_BLE_WRITE_AF01 = '0000af01-0000-1000-8000-00805f9b34fb'
UUID_BLE_WRITE_AF02 = '0000af02-0000-1000-8000-00805f9b34fb'
# Used for firmware update
UUID_BLE_WRITE_FEE1 = '0000fee1-0000-1000-8000-00805f9b34fb'

UUID_DESCRIPTOR = '00002902-0000-1000-8000-00805f9b34fb'

ID_MANUFACTURER = 0xabba
MAGIC_MANUFACTURER = b'\xaf\xfa'

def _field(struct_type, **kwargs):
    return field(metadata={STRUCT_TYPE: '<'+struct_type}, **kwargs)

@dataclass(frozen=True)
class DeviceInfo:
    device_model_id: str
    user_name: str
    oem_id: str
    device: str
    # "C" for charging, "S" for nothing connected
    work_state: str
    # One character for each output, "C" for charging, "S" for nothing connected
    binding_action: str
    add_state: str

    @classmethod
    def from_advertisement(cls, advertisement):
        #manufacturer_data={43962: b'\xaf\xfa\x01\x0e\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'}
        manufacturer_data = advertisement.manufacturer_data.get(ID_MANUFACTURER)
        name = advertisement.local_name
        if manufacturer_data and manufacturer_data[:2] == MAGIC_MANUFACTURER:
            # TODO: The length of binding_action varies by device:
            # PS200L, PS200H, PS200X: 4 (seems to be 5?)
            # PB40, MAG20: 2 + length 3 string CH1
            # PB80W, PB50DW: 4 + length 3 string CH1
            # EDGE, GP68C2, ZIP: 3
            # PB10DW, PB25DW: 3 + length 3 string CH1
            # default: 2 + length 3 string CH1 + length 3 string CH2
            return cls(
                device_model_id=manufacturer_data[2:6].hex(),
                user_name=manufacturer_data[6:].rstrip(b'\0').decode(),
                oem_id=name[:4],
                device=name[4:12].strip(),
                work_state=name[12] or 'S',
                binding_action=name[13:13+5],
                add_state=name[25],
            )
        raise ValueError("Device is not ISDT")

def af02_packet(cmd):
    _cmd = cmd
    @dataclass
    class AF02Packet:
        cmd: int = csfield(cs.Const(bytes([_cmd])))
    def wrap(cls):
        return dataclass_struct(make_dataclass(cls.__name__, (), bases=(dataclass(cls), AF02Packet)))
    return wrap

_CMD_CLASSES = dict()

def req(cmd, direction=0x12):
    _cmd, _direction = cmd, direction
    @dataclass
    class Req:
        direction: int = csfield(cs.Const(bytes([_direction])))
        cmd: int = csfield(cs.Const(bytes([_cmd])))
    def wrap(cls):
        out = dataclass_struct(make_dataclass(cls.__name__, (), bases=(dataclass(cls), Req)))
        _CMD_CLASSES[cmd] = out
        return out
    return wrap

def resp(cmd, direction=0x31):
    return req(cmd, direction)

class UUIDAdapter(cs.Adapter):
    def _decode(self, obj, context, path):
        return UUID(bytes=obj)
    def _encode(self, obj, context, path):
        return obj.bytes

@af02_packet(0x18)
class BindReq:
    uuid: bytes = csfield(UUIDAdapter(cs.Bytes(16)))
    trailer: bytes = csfield(cs.Const(b"\x00"))
@af02_packet(0x19)
class BindResp:
    bound: int = csfield(cs.Byte)
@af02_packet(0xE0)
class BLEHardwareInfoReq:
    pass
@af02_packet(0xE1)
class BLEHardwareInfoResp:
    main_hardware_version: int = csfield(cs.Byte)
    sub_hardware_version: int = csfield(cs.Byte)
    main_software_version: int = csfield(cs.Byte)
    sub_software_version: int = csfield(cs.Byte)
    device_id: int = csfield(cs.Bytes(8))

@req(0x92)
class AlarmToneReq:
    pass
@resp(0x93)
class AlarmToneResp:
    state: bool = csfield(cs.Flag)
@req(0x94)
class PS200WorkingStatusReq:
    pass
@dataclass_struct
class PS200WorkingStatusChannel:
    channel_id: int = csfield(cs.Byte)
    valid_id: int = csfield(cs.Byte)
    channel_type: int = csfield(cs.Byte)
    fast_charge_protocol: int = csfield(cs.Byte)
    reserved_value: int = csfield(cs.Bytes(2))
    output_voltage: int = csfield(cs.Int32ul)
    output_current: int = csfield(cs.Int32ul)
    output_power: int = csfield(cs.Int32ul)
    maximum_power: int = csfield(cs.Int32ul)
    current_power: int = csfield(cs.Int32ul)
    work_time: int = csfield(cs.Int32ul)
    mWh: int = csfield(cs.Int32ul)
@resp(0x95)
class PS200WorkingStatusResp:
    total_channels: int = csfield(cs.Byte)
    timestamp: int = csfield(cs.Int32ul)
    channels: list[PS200WorkingStatusChannel] = subcsfield(
        PS200WorkingStatusChannel,
        cs.Array(cs.this.total_channels, PS200WorkingStatusChannel.struct),
    )
@req(0x96)
class PS200DCStatusReq:
    pass
@dataclass_struct
class PS200DCStatusChannel:
    channel_types: int = csfield(cs.Byte)
    valid_id: int = csfield(cs.Byte)
    voltage: int = csfield(cs.Int32ul)
    current: int = csfield(cs.Int32ul)
    maximum_power: int = csfield(cs.Int32ul)
    current_power: int = csfield(cs.Int32ul)
    current_set_power: int = csfield(cs.Int32ul)
@resp(0x97)
class PS200DCStatusResp:
    # N.B. timestamp and total_channels have the opposite order from PS200WorkingStatusResp
    timestamp: int = csfield(cs.Int32ul)
    total_channels: int = csfield(cs.Byte)
    channels: list[PS200DCStatusChannel] = subcsfield(
        PS200DCStatusChannel,
        cs.Array(cs.this.total_channels, PS200DCStatusChannel.struct),
    )
@req(0x9C, direction=0x13)
class AlarmToneTaskReq:
    task_type: int = csfield(cs.Byte)
@req(0x9D)
class AlarmToneTaskResp:
    status: int = csfield(cs.Byte) # TODO: true if == -1

# 0xC1 RenameResp
# 0xE1 HardwareInfoResp
# 0xE5 ElectricResp
# 0xE7 ChargerWorkStateResp
# 0xEB WorkTasksResp
# 0xFB IRResp
# 0xF1 OTAUpgradeCmdResp
# 0xF3 OTAEraseResp
# 0xF5 OTAWriteResp
# 0xF7 OTAChecksumResp
# 0xFD OTARebootResp

@contextlib.asynccontextmanager
async def connect(device: BLEDevice, info: DeviceInfo):
    device_uuid = uuid4()
    connected = False
    client = await establish_connection(BleakClient, device, device.address)
    try:
        serviceFEE0 = client.services.get_service(UUID_BLE_SERVICE_FEE0)
        serviceAF00 = client.services.get_service(UUID_BLE_SERVICE_AF00)
        if serviceAF00:
            characteristicAF01 = serviceAF00.get_characteristic(UUID_BLE_WRITE_AF01)
            characteristicAF02 = serviceAF00.get_characteristic(UUID_BLE_WRITE_AF02)
            charger = BluetoothCharger(client, info, characteristicAF01, characteristicAF02)
            await client.start_notify(characteristicAF01, charger._af01_callback)
            await client.start_notify(characteristicAF02, charger._af02_callback)
            descriptor = characteristicAF01.get_descriptor(UUID_DESCRIPTOR)
            if descriptor:
                try:
                    await client.write_gatt_descriptor(descriptor.handle, b"\x01\x00") # ENABLE_NOTIFICATION_VALUE
                except:
                    logging.exception("failed to set ENABLE_NOTIFICATION_VALUE")
            yield charger
    finally:
        await client.disconnect()
    raise ValueError("service/characteristics not found")

class BluetoothCharger:
    def __init__(self, client: BleakClient, info: DeviceInfo, characteristicAF01, characteristicAF02):
        self.client = client
        self.info = info
        self.characteristicAF01 = characteristicAF01
        self.characteristicAF02 = characteristicAF02
        self.af01_future = None

    def _af02_callback(self, sender: BleakGATTCharacteristic, data: bytearray):
        obj = data
        match tuple(data[0:1]):
            case (0x19, _):
                obj = BindResp.parser.parse(data)
            case (0xE1, _):
                obj = BLEHardwareInfoResp.parser.parse(data)
            case (_, 0xF5):
                # OTA Write response
                pass
        logging.info("AF02: %s", obj)

    def _af01_callback(self, sender: BleakGATTCharacteristic, data: bytearray):
        if cls := _CMD_CLASSES.get(data[1]):
            obj = cls.parser.parse(data)
            if self.af01_future is not None:
                self.af01_future.set_result(obj)
                self.af01_future = None
            else:
                logging.warning("Unexpected AF01: %s", obj)
        else:
            logging.warning("Unknown AF01 message: %s", data)

    @retry_bluetooth_connection_error()
    async def _write_char(self, characteristic, packet):
        if not isinstance(packet, bytes):
            packet = packet.parser.build(packet)
        await self.client.write_gatt_char(characteristic, packet)

    async def request_af01(self, packet):
        fut = asyncio.get_running_loop().create_future()
        self.af01_future = fut
        await self._write_char(self.characteristicAF01, packet)
        return await fut

    async def write_af02(self, packet):
        await self._write_char(self.characteristicAF02, packet)

    async def get_ble_hardware_info(self):
        await self.write_af02(BLEHardwareInfoReq())

    async def get_ps200_dc_status(self):
        return await self.request_af01(PS200DCStatusReq())

    async def get_ps200_working_status(self):
        return await self.request_af01(PS200WorkingStatusReq())

async def enumerate_devices():
    async with BleakScanner(
        service_uuids=[
            UUID_BLE_SERVICE_AF00,
            UUID_BLE_SERVICE_FEE0,
        ],
    ) as scanner:
        async for device, advertisement in scanner.advertisement_data():
            try:
                yield device, advertisement, DeviceInfo.from_advertisement(advertisement)
            except ValueError:
                pass

async def main():
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("bleak.backends.bluezdbus.manager").setLevel(logging.INFO)
    async for device, advertisement, info in enumerate_devices():
        print(device, advertisement, info)
        while True:
            try:
                async with connect(device, info) as charger:
                    for service in charger.client.services.services.values():
                        print("Service:", service.uuid)
                        for characteristic in service.characteristics:
                            print("Characteristic:", characteristic.uuid)
                    await charger.get_ble_hardware_info()
                    while True:
                        logging.info("DC status: %s", await charger.get_ps200_dc_status())
                        logging.info("Working status: %s", await charger.get_ps200_working_status())
                        await asyncio.sleep(1)
            except BLEAK_RETRY_EXCEPTIONS:
                logging.warning("connection lost", exc_info=True)
            await asyncio.sleep(1)
if __name__ == '__main__':
    asyncio.run(main())
