import os
import random
import socket
import struct
import time
from datetime import datetime

# type of ICMP message, for echo request it's 8
TYPE = 8

# subtype of ICMP message, not used by echo request
CODE = 0

# timeout in seconds for echo replies
TIMEOUT = 2

# buffer size for echo replies
RECEIVE_MAX_SIZE = 1024

# time between subsequent echo requests to all hosts
WAIT_TIME = 2

# codes for communicating abnormal status
TIMEOUT_CODE = -1
WRONG_ANSWER_CODE = -2
SOCKET_ERROR_CODE = -3
ICMP_ERROR_CODE = -4


def ones_complement_sum(data):
    """
    Calculates 16-bit ones complement sum of an even number of bytes.
    :param data: the array of bytes on which to calculate
    :return: the computed sum
    """
    if (len(data) % 2) != 0:
        raise Exception("Data must have an even number of bytes")
    else:
        ocs = 0  # initialize the sum to 0
        for i in range(0, len(data), 2):
            '''
            calculates the sum rearranging data in 16-bit words
            '''
            ocs += (
                    (data[i] << 8) +  # we move the first byte of the pair to be the 8 most significant bits of the word
                    data[i + 1]  # the second byte of the pair becomes the 8 least significant bits
            )
        '''
        FROM RFC 1071: 
        There are further coding techniques that can be exploited to speed up
        the checksum calculation.
        (1)  Deferred Carries
        Depending upon the machine, it may be more efficient to defer
        adding end-around carries until the main summation loop is
        finished.

        Here, we proceed to add all carries until we're left with a four byte number.
        '''
        while ocs >> 16:  # while there are bits set to one past the first word of the number
            ocs = (ocs & 0xffff) + (ocs >> 16)  # sum the carry to the first word of the number
        return ocs


def checksum(data):
    """
    Calculates internet checksum of an even number of bytes.
    :param data: the array of bytes of which to compute the checksum
    :return: the computed checksum
    """
    if (len(data) % 2) != 0:
        '''
        we could add a 0x00 byte at the end to account for this situation but it is not necessary for this script
        '''
        raise Exception("Data must have an even number of bytes")
    else:
        '''
        In outline, the Internet checksum algorithm is very simple:

        (1)  Adjacent octets to be checksummed are paired to form 16-bit
             integers, and the 1's complement sum of these 16-bit integers is
             formed.
        '''
        ocs = ones_complement_sum(data)

        '''
        (2)  To generate a checksum... ...the 16-bit 1's complement sum is computed over the octets
        concerned, and the 1's complement of this sum is placed in the
        checksum field.
        '''
        return (~ocs) & 0xffff  # & 0xffff removes the bits beyond the first word


def compose_echo_message(identifier, sequence_number):
    """
    Builds an icmp echo request with given identifier and sequence number
    :param identifier: the identifier of the echo request
    :param sequence_number: the sequence number of the echo request
    :return: the message if all goes well, otherwise throws an exception
    """

    '''
    we generate a temporary message with checksum field set to 0 to compute the checksum
    ! = network endianness
    B = unsigned char (byte)
    H = unsigned short (2 bytes)
    see the pdf for more information
    '''
    temp_message = struct.pack("!BBHHH", TYPE, CODE, 0, identifier, sequence_number)
    cs = checksum(temp_message)
    message = struct.pack("!BBHHH", TYPE, CODE, cs, identifier, sequence_number)
    '''
    (3)  To check a checksum, the 1's complement sum is computed over the
    same set of octets, including the checksum field.  If the result
    is all 1 bits (-0 in 1's complement arithmetic), the check
    succeeds.
    '''
    check = ones_complement_sum(message)
    if check == 0xffff:
        return message
    else:
        raise ValueError("Error while computing checksum.")


def read_icmp_message(data):
    """
    Reads the identifier and sequence number if data contains a valid ICMP reply
    for a request generated by compose_echo_message.
    :param data: the message to parse
    :return: the identifier and sequence if data is an ICMP reply
    """

    '''
    here we check that the data is not corrupted by verifying the checksum
    '''
    if ones_complement_sum(data) == 0xffff:
        try:
            message_type, code, cs, identifier, sequence_number = struct.unpack("!BBHHH", data)
            if message_type == 0 and code == 0:
                return identifier, sequence_number
        except Exception:
            raise ValueError("data contains a number of bytes != 8")
    return None, None  # message_type or code were != 0 or checksum was not correct


def perror(arg):
    """
    Prints an error message
    :param arg: the message to print
    """
    print("[ERROR]\t" + arg + "\n")


def pinfo(arg):
    """
    Prints an informational message
    :param arg: the message to print
    """
    print("[INFO]\t" + arg + "\n")


def get_desc(status):
    """
    Returns the description of an abnormal status
    :param status: the status < 0
    :return: a string describing the status.
    """
    if status == TIMEOUT_CODE:
        return "timed out."
    elif status == WRONG_ANSWER_CODE:
        return "errors in received echo reply."
    elif status == SOCKET_ERROR_CODE:
        return "error while creating the socket."
    elif status == ICMP_ERROR_CODE:
        return "error while generating icmp echo request."
    else:
        return "unknown status."


def pstatus(address, t, name=None):
    """
    Prints an informational string about a given address
    :param address: the address
    :param t: the status of this address
    :param name: the name given by the user as input
    :return:
    """
    print(
        "\t" + address +
        (" (" + name + ")" if name is not None else "") +  # this line adds the mnemonic address if one was provided
        ((" is reachable, ping: " + (str(t) if t > 1 else "<1") + " ms") if t >= 0 else
         (" is not reachable: " + get_desc(t)))
    )


def pheader():
    """
    Prints a header for the informational strings about the hosts
    """
    print("\n" * 50 +  # os.system("clear || cls") does not work with some IDEs
          "Requesting each host for echo every " + str(WAIT_TIME) + " seconds.\nPress Ctrl+C to exit.\n"
          + "\n"
          + "\t[LATEST REFRESH] " + datetime.now().strftime("%H:%M:%S"))


def ping_once(address, identifier, sequence_number):
    """
    Pings the host and returns it status
    :param address: the address to ping
    :param identifier: the identifier to use in icmp request
    :param sequence_number: the sequence number to use in icmp request
    :return: an integer indicating the status of the host
    A status >0 means the host is reachable and its ping was the returned
    value in milliseconds, or <0 means the host is not reachable or an error
    occurred while trying to send or receive the icmp message.
    """
    try:
        '''
        The address format required by a particular socket object is automatically selected based on the address family
        specified when the socket object was created. A pair (host, port) is used for the AF_INET address family,
        where host is a string representing either a hostname in internet domain notation like 'daring.cwi.nl' or an
        IPv4 address like '100.50.200.5', and port is an integer.

        socket.SOCK_RAW represents the socket type

        socket.IPPROTO_ICMP specifies the protocol:
        this value/number is what the IP layer will write to the
        protocol_type field in its header to define the upper level protocol. It is the
        "Protocol" field of the IP packet.
        '''
        s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        s.settimeout(TIMEOUT)  # timeout for recvfrom()
    except socket.error:
        return SOCKET_ERROR_CODE  # error in creating the socket
    try:
        sent = compose_echo_message(identifier, sequence_number)
    except ValueError:
        return ICMP_ERROR_CODE  # error in composing the message

    '''
    for a detailed discussion of how this works see the attached pdf file
    '''
    try:
        '''
        For the AF_INET address family an address is composed of both an address and a port, 
        so it must be specified even if neither IP nor ICMP use ports.
        '''
        s.sendto(sent, (address, 0))  # port is arbitrary and insignificant
        send_time = time.time()  # saving send time
        answer, recv_addr = s.recvfrom(RECEIVE_MAX_SIZE)
        '''
        we save this even if we don't know whether the answer 
        is valid so as not to consider elaboration time when 
        giving the user a ping value
        '''
        receive_delay = time.time() - send_time
        ip_version = answer[0] >> 4
        protocol = answer[9]
        '''
        here we check
        - the sender of the received packet is the host we are pinging
        - the IP protocol version of the received packet is 4
        - the protocol field contains 1 (socket.IPPROTO_ICMP) which stands for ICMP protocol
        '''
        if recv_addr[0] == address and ip_version == 4 and protocol == socket.IPPROTO_ICMP:
            header_length = (answer[0] & 0x0f) * 4  # ihl is saved in 32-bit words
            message = answer[header_length:]
            try:
                answer_identifier, answer_sequence_number = read_icmp_message(message)
                if answer_identifier == identifier and answer_sequence_number == sequence_number:
                    # the answer is correct, we return the elapsed time
                    return int(receive_delay * 1000)
            except ValueError:  # icmp message was ill formatted
                pass
        return WRONG_ANSWER_CODE
    except socket.timeout:  # recvfrom timed out
        return TIMEOUT_CODE
    except socket.error:  # unexpected error from the socket
        return SOCKET_ERROR_CODE


def main():
    """
    The main function of the script, which provides a simple
    command line user interface to ping multiple hosts indefinitely.
    """
    # 0xffff is the max int which can fit in 4 bytes
    sequence_number = random.randint(0, 0xffff + 1)
    identifier = (os.getpid() & 0xffff)
    # contains the association between IPv4 and mnemonic addresses
    names = {}
    # contains the latest status of each host after each update
    status = {}
    print("Insert addresses separated by newlines or an empty line to ping: \n")
    while True:  # data input phase
        host = input()  # read the input line
        if len(host):  # something was written
            try:
                address = socket.gethostbyname(host)  # will initiate a dns search if address is not IPv4
                if address != host:  # host was a mnemonic address
                    names[address] = host  # add the mnemonic address to the dictionary
                    pinfo("Hostname " + host + " resolved to " + address + " and correctly added.")
                else:  # host was already an IPv4 address
                    names[address] = None  # no mnemonic name was supplied
                    pinfo("Address correctly added.")
            except socket.gaierror:  # address was not IPv4 and dns search produced nothing
                perror(
                    "Address " + host +
                    " is not a valid IPv4 address and DNS search returned nothing: address not added")
        else:  # empty line
            if len(names.keys()):  # at least one address was specified
                pinfo("Address(es) accepted.")
                break
            else:  # can't start monitoring an empty list of addresses
                perror("No address has been added yet.")

    while True:  # host ping phase
        for address in names:
            status[address] = ping_once(address, identifier, sequence_number)  # update the address' status
        pheader()  # prints a simple status message
        for address in names:  # printed in a single pass after every ping has finished for readability reasons
            pstatus(address, status[address], names[address])  # prints the address' status
        time.sleep(WAIT_TIME)  # we give the user time to read


'''
even though this will never be used as a module, it's good practice
'''
if __name__ == "__main__":
    main()
