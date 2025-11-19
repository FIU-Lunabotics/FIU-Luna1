package main

import (
    "encoding/binary"
    "hash/crc32"
)

// MaxPacketSize is the maximum allowed payload size (in bytes) for a single packet.
var MaxPacketSize = 8192

func ComputeCRC(data []byte) uint32 {
    return crc32.ChecksumIEEE(data)
}

func AppendCRC(data []byte) []byte {
    c := ComputeCRC(data)
    out := make([]byte, len(data)+4)
    copy(out, data)
    binary.BigEndian.PutUint32(out[len(data):], c)
    return out
}

func VerifyPacket(payloadWithCRC []byte) (payload []byte, ok bool) {
    if len(payloadWithCRC) < 4 {
        return nil, false
    }
    payloadLen := len(payloadWithCRC) - 4
    payload = make([]byte, payloadLen)
    copy(payload, payloadWithCRC[:payloadLen])
    expected := binary.BigEndian.Uint32(payloadWithCRC[payloadLen:])
    return payload, ComputeCRC(payload) == expected
}
