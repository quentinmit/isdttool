# coding=utf-8

"""This file contains functions to connect to BLE chargers."""
import asyncio
import contextlib
from dataclasses import dataclass, field

from bleak import BleakScanner, BleakClient, BleakGATTCharacteristic, AdvertisementData, BLEDevice

# Usually FW Update service
UUID_BLE_SERVICE_FEE0 = '0000fee0-0000-1000-8000-00805f9b34fb'
# Main charger data
UUID_BLE_SERVICE_AF00 = '0000af00-0000-1000-8000-00805f9b34fb'
UUID_BLE_SERVICE_DB00 = '0000db00-0000-1000-8000-00805f9b34fb'

UUID_BLE_WRITE_AF01 = '0000af01-0000-1000-8000-00805f9b34fb'
UUID_BLE_WRITE_AF02 = '0000af02-0000-1000-8000-00805f9b34fb'
# Used for firmware update
UUID_BLE_WRITE_FEE1 = '0000fee1-0000-1000-8000-00805f9b34fb'

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

@contextlib.asynccontextmanager
async def connect(device: BLEDevice, info: DeviceInfo):
    async with BleakClient(device) as client:
        serviceFEE0 = client.services.get_service(UUID_BLE_SERVICE_FEE0)
        serviceAF00 = client.services.get_service(UUID_BLE_SERVICE_AF00)
        if serviceAF00:
            characteristicAF01 = serviceAF00.get_characteristic(UUID_BLE_WRITE_AF01)
            characteristicAF02 = serviceAF00.get_characteristic(UUID_BLE_WRITE_AF02)
            charger = BluetoothCharger(client, info)
            await client.start_notify(characteristicAF01, charger._af01_callback)
            await client.start_notify(characteristicAF02, charger._af02_callback)
            yield charger

class BluetoothCharger:
    def __init__(self, client: BleakClient, info: DeviceInfo):
        self.client = client
        self.info = info

    def _af01_callback(self, sender: BleakGATTCharacteristic, data: bytearray):
        match tuple(data[0:1]):
            case (25, _):
                # Bind response
                pass
            case (225, _):
                # Hardware info response
                pass
            case (_, 245):
                # OTA Write response
                pass
        print(f"{sender}: {data}")

    def _af02_callback(self, sender: BleakGATTCharacteristic, data: bytearray):
        # data[1]:
        # 0x93 AlarmToneResp
        # 0x95 PS200WorkingStatusResp
        # 0x97 PS200DCStatusResp
        # 0x9D AlarmToneTaskResp
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
        print(f"{sender}: {data}")

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
    async for device, advertisement, info in enumerate_devices():
        print(device, advertisement, info)
        async with connect(device, info) as charger:
            for service in charger.client.services.services.values():
                print("Service:", service.uuid)
                for characteristic in service.characteristics:
                    print("Characteristic:", characteristic.uuid)
            await asyncio.sleep(30)
            return

if __name__ == '__main__':
    asyncio.run(main())
