#!/usr/bin/env python

import re
import time
from libs.util import tc
from pprint import pformat
from libs.peer_ops import peer_probe
from libs.mount_ops import mount_volume
try:
    import xml.etree.cElementTree as etree
except ImportError:
    import xml.etree.ElementTree as etree
from libs.quota_ops import enable_quota, set_quota_limit
from tests_d.uss.uss_libs import set_uss
from libs.gluster_init import env_setup_servers, start_glusterd

"""
    This file contains the gluster volume operations like create volume,
    start/stop volume
"""

def create_volume(volname, dist, rep=1, stripe=1, trans='tcp', servers='', \
        snap=True, disp=1, dispd=1, red=1):
    """
        Create the gluster volume specified configuration
        volname and distribute count are mandatory argument
    """
    if servers == '':
        servers = tc.nodes[:]
    dist = int(dist)
    rep = int(rep)
    stripe = int(stripe)
    disp = int(disp)
    dispd = int(dispd)
    red = int(red)
    dispc = 1

    if disp != 1 and dispd != 1:
        tc.logger.error("volume can't have both disperse and disperse-data")
        return (-1, None, None)
    if disp != 1:
        dispc = int(disp)
    elif dispd != 1:
        dispc = int(dispd) + int(red)

    number_of_bricks = dist * rep * stripe * dispc
    replica = stripec = disperse = disperse_data = redundancy = ''
    brick_root = '/bricks'
    n = 0
    tempn = 0
    bricks_list = ''
    rc = tc.run(servers[0], "gluster volume info | egrep \"^Brick[0-9]+\"", \
            verbose=False)
    for i in range(0, number_of_bricks):
        if not snap:
            bricks_list = "%s %s:%s/%s_brick%d" % \
                (bricks_list, servers[n], brick_root, volname, i)
        else:
            sn = len(re.findall(servers[n], rc[1])) + tempn
            bricks_list = "%s %s:%s/brick%d/%s_brick%d" % \
            (bricks_list, servers[n], brick_root, sn, volname, i)
        if n < len(servers[:]) - 1:
            n = n + 1
        else:
            n = 0
            tempn = tempn + 1

    if rep != 1:
        replica = "replica %d" % rep
    if stripe != 1:
        stripec = "stripe %d" % stripe
    ttype = "transport %s" % trans
    if disp != 1:
        disperse = "disperse %d" % disp
        redundancy = "redundancy %d" % red
    elif dispd != 1:
        disperse_data = "disperse-data %d" % dispd
        redundancy = "redundancy %d" % red

    ret = tc.run(servers[0], "gluster volume create %s %s %s %s %s %s %s %s \
--mode=script" % (volname, replica, stripec, disperse, disperse_data, \
redundancy, ttype, bricks_list))
    return ret

def start_volume(volname, mnode='', force=False):
    """
        Starts the gluster volume
        Returns True if success and False if failure
    """
    if mnode == '':
        mnode = tc.nodes[0]
    frce = ''
    if force:
        frce = 'force'
    ret = tc.run(mnode, "gluster volume start %s %s" % (volname, frce))
    if ret[0] != 0:
        return False
    return True


def stop_volume(volname, mnode='', force=False):
    """
        Stops the gluster volume
        Returns True if success and False if failure
    """
    if mnode == '':
        mnode = tc.nodes[0]
    frce = ''
    if force:
        frce = 'force'
    ret = tc.run(mnode, "gluster volume stop %s %s --mode=script" \
            % (volname, frce))
    if ret[0] != 0:
        return False
    return True


def delete_volume(volname, mnode=''):
    """
        Deletes the gluster volume
        Returns True if success and False if failure
    """
    if mnode == '':
        mnode = tc.nodes[0]
    volinfo = get_volume_info(volname, mnode)
    bricks = volinfo[volname]['bricks']
    ret = tc.run(mnode, "gluster volume delete %s --mode=script" % volname)
    if ret[0] != 0:
        return False
    try:
        del tc.global_flag[volname]
    except KeyError:
        pass
    for brick in bricks:
        node, vol_dir = brick.split(":")
        ret = tc.run(node, "rm -rf %s" % vol_dir)

    return True


def setup_meta_vol(servers=''):
    """
        Creates, starts and mounts the gluster meta-volume on the servers
        specified.
    """
    if servers == '':
        servers = tc.nodes
    meta_volname = 'gluster_shared_storage'
    mount_point = '/var/run/gluster/shared_storage'
    metav_dist = int(tc.config_data['META_VOL_DIST_COUNT'])
    metav_rep = int(tc.config_data['META_VOL_REP_COUNT'])
    _num_bricks = metav_dist * metav_rep
    repc = ''
    if metav_rep > 1:
        repc = "replica %d" % metav_rep
    bricks = ''
    brick_root = "/bricks"
    _n = 0
    for i in range(0, _num_bricks):
        bricks = "%s %s:%s/%s_brick%d" % (bricks, servers[_n], \
                brick_root, meta_volname, i)
        if _n < len(servers) - 1:
            _n = _n + 1
        else:
            _n = 0
    gluster_cmd = "gluster volume create %s %s %s force" \
            % (meta_volname, repc, bricks)
    ret = tc.run(servers[0], gluster_cmd)
    if ret[0] != 0:
        tc.logger.error("Unable to create meta volume")
        return False
    ret = start_volume(meta_volname, servers[0])
    if not ret:
        tc.logger.error("Unable to start the meta volume")
        return False
    time.sleep(5)
    for server in servers:
        ret = mount_volume(meta_volname, 'glusterfs', mount_point, server, \
                server)
        if ret[0] != 0:
            tc.logger.error("Unable to mount meta volume on %s"% server)
            return False
    return True


def setup_vol(volname='', dist='', rep='', dispd='', red='', stripe='', trans='', \
        servers=''):
    """
        Setup a gluster volume for testing.
        It first formats the back-end bricks and then creates a
        trusted storage pool by doing peer probe. And then it creates
        a volume of specified configuration.

        When the volume is created, it sets a global flag to indicate
        that the volume is created. If another testcase calls this
        function for the second time with same volume name, the function
        checks for the flag and if found, will return True.

        Returns True on success and False for failure.
    """
    if volname == '':
        volname = tc.config_data['VOLNAME']
    if dist == '':
        dist = tc.config_data['DIST_COUNT']
    if rep == '':
        rep = tc.config_data['REP_COUNT']
    if dispd == '':
        dispd = tc.config_data['DISPERSE']
    if red == '':
        red = tc.config_data['REDUNDANCY']
    if stripe == '':
        stripe = tc.config_data['STRIPE']
    if trans == '':
        trans = tc.config_data['TRANS_TYPE']
    if servers == '':
        servers = tc.nodes
    try:
        if tc.global_flag[volname] == True:
            tc.logger.debug("The volume %s is already created. Returning..." \
                    % volname)
            return True
    except KeyError:
        tc.logger.info("The volume %s is not present. Creating it" % volname)
    ret = env_setup_servers(servers=servers)
    if not ret:
        tc.logger.error("Formatting backend bricks failed. Aborting...")
        return False
    ret = start_glusterd(servers)
    if not ret:
        tc.logger.error("glusterd did not start in at least one server")
        return False
    ret = peer_probe(servers[0], servers[1:])
    if not ret:
        tc.logger.error("Unable to peer probe one or more machines")
        return False
    if rep != 1 and dispd != 1:
        tc.logger.warning("Both replica count and disperse count is specified")
        tc.logger.warning("Ignoring the disperse and using the replica count")
        dispd = 1
        red = 1
    ret = create_volume(volname, dist, rep, stripe, trans, servers, \
            dispd=dispd, red=red)
    if ret[0] != 0:
        tc.logger.error("Unable to create volume %s" % volname)
        return False
    time.sleep(2)
    if tc.config_data['ENABLE_QUOTA'] == 'True':
        ret0 = enable_quota(volname, servers[0])
        ret1 = set_quota_limit(volname, server=servers[0])
        if ret0[0] != 0 or ret1[0] != 0:
            tc.logger.error("Quota setup failed")
            return False
    if tc.config_data['ENABLE_USS'] == 'True':
        ret = set_uss(volname, 'enable', servers[0])
        if not ret:
            tc.logger.error("Unable to enable USS for volume %s" % volname)
            return False
    ret = start_volume(volname, servers[0])
    if not ret:
        tc.logger.error("volume start %s failed" % volname)
        return False
    tc.global_flag[volname] = True
    return True


def _parse_volume_status_xml(root_xml):
    """
    Helper module for get_volume_status. It takes root xml object as input,
    parses and returns the 'volume' tag xml object.
    """
    for element in root_xml:
        if element.findall("volume"):
            return element.findall("volume")
        root_vol = _parse_volume_status_xml(element)
        if root_vol is not None:
            return root_vol

def parse_xml(tag_obj):
    """
    This module takes any xml element object and parses all the child nodes
    and returns the parsed data in dictionary format
    """
    node_dict = {}
    for tag in tag_obj:
        if re.search(r'\n\s+', tag.text) is not None:
            port_dict = {}
            port_dict = parse_xml(tag)
            node_dict[tag.tag] = port_dict
        else:
            node_dict[tag.tag] = tag.text
    return node_dict

def get_volume_status(server='', volname='all', service='', options=''):
    """
    This module gets the status of all or specified volume(s)/brick
    @parameter:
        * server  - <str> (optional) name of the server to execute the volume
                    status command. If not given, the function takes the
                    first node from config file
        * volname - <str> (optional) name of the volume to get status. It not
                    given, the function returns the status of all volumes
        * service - <str> (optional) name of the service to get status.
                    serivce can be, [nfs|shd|<BRICK>|quotad]], If not given,
                    the function returns all the services
        * options - <str> (optional) options can be,
                    [detail|clients|mem|inode|fd|callpool|tasks]. If not given,
                    the function returns the output of gluster volume status
    @Returns: volume status in dict of dictionary format, on success
              None, on failure
    """

    if server == '':
        server = tc.nodes[0]

    cmd = "gluster vol status %s %s %s --xml" % (volname, service, options)

    ret = tc.run(server, cmd)
    if ret[0] != 0:
        tc.logger.error("Failed to execute gluster volume status command")
        return None

    root = etree.XML(ret[1])
    volume_list = _parse_volume_status_xml(root)
    if volume_list is None:
        tc.logger.error("No volumes exists in the gluster")
        return None

    vol_status = {}
    for volume in volume_list:
        tmp_dict1 = {}
        tmp_dict2 = {}
        vol_name = [vol.text for vol in volume if vol.tag == "volName"]

        # parsing volume status xml output
        if options == 'tasks':
            tasks = volume.findall("tasks")
            for each_task in tasks:
                tmp_dict3 = parse_xml(each_task)
                node_name = 'task_status'
                if 'task' in tmp_dict3.keys():
                    if node_name in tmp_dict2.keys():
                        tmp_dict2[node_name].append(tmp_dict3['task'])
                    else:
                        tmp_dict2[node_name] = [tmp_dict3['task']]
                else:
                    tmp_dict2[node_name] = [tmp_dict3]
        else:
            nodes = volume.findall("node")
            for each_node in nodes:
                if each_node.find('path').text.startswith('/'):
                    node_name = each_node.find('hostname').text
                else:
                    node_name = each_node.find('path').text
                node_dict = parse_xml(each_node)
                tmp_dict3 = {}
                if "hostname" in node_dict.keys():
                    if node_dict['path'].startswith('/'):
                        tmp = node_dict["path"]
                        tmp_dict3[node_dict["path"]] = node_dict
                    else:
                        tmp_dict3[node_dict["hostname"]] = node_dict
                        tmp = node_dict["hostname"]
                    del tmp_dict3[tmp]["path"]
                    del tmp_dict3[tmp]["hostname"]

                if node_name in tmp_dict1.keys():
                    tmp_dict1[node_name].append(tmp_dict3)
                else:
                    tmp_dict1[node_name] = [tmp_dict3]

                tmp_dict4 = {}
                for item in tmp_dict1[node_name]:
                    for key, val in item.items():
                        tmp_dict4[key] = val
                tmp_dict2[node_name] = tmp_dict4

        vol_status[vol_name[0]] = tmp_dict2
    tc.logger.debug("Volume status output: %s" \
                    % pformat(vol_status, indent=10))
    return vol_status

def get_volume_option(volname, option='all', server=''):
    """
    This module gets the option values for the given volume.
    @parameter:
        * volname - <str> name of the volume to get status.
        * option  - <str> (optional) name of the volume option to get status.
                    If not given, the function returns all the options for
                    the given volume
        * server  - <str> (optional) name of the server to execute the volume
                    status command. If not given, the function takes the
                    first node from config file
    @Returns: value for the given volume option in dict format, on success
              None, on failure
    """
    if server == '':
        server = tc.nodes[0]

    cmd = "gluster volume get %s %s" % (volname, option)
    ret = tc.run(server, cmd)
    if ret[0] != 0:
        tc.logger.error("Failed to execute gluster volume get command")
        return None

    volume_option = {}
    raw_output = ret[1].split("\n")
    for line in raw_output[2:-1]:
        match = re.search(r'^(\S+)(.*)', line.strip())
        if match is None:
            tc.logger.error("gluster get volume output is not in \
                             expected format")
            return None

        volume_option[match.group(1)] = match.group(2).strip()

    return volume_option


def get_volume_info(volname='all', server=''):
    """
        Fetches the volume information as displayed in the volume info.
        Uses xml output of volume info and parses the into to a dict

        Returns a dict of dicts.
        -- Volume name is the first key
        -- distCount/replicaCount/Type etc are second keys
        -- The value of the each second level dict depends on the key
        -- For distCount/replicaCount etc the value is key
        -- For bricks, the value is a list of bricks (hostname:/brick_path)
    """
    if server == '':
        server = tc.nodes[0]
    ret = tc.run(server, "gluster volume info %s --xml" % volname, \
            verbose=False)
    if ret[0] != 0:
        tc.logger.error("volume info returned error")
        return None
    root = etree.XML(ret[1])
    volinfo = {}
    for volume in root.findall("volInfo/volumes/volume"):
        for elem in volume.getchildren():
            if elem.tag == "name":
                volname = elem.text
                volinfo[volname] = {}
            elif elem.tag == "bricks":
                volinfo[volname]["bricks"] = []
                for el in elem.getiterator():
                    if el.tag == "name":
                        volinfo[volname]["bricks"].append(el.text)
            elif elem.tag == "options":
                volinfo[volname]["options"] = {}
                for option in elem.findall("option"):
                    for el in option.getchildren():
                        if el.tag == "name":
                            opt = el.text
                        if el.tag == "value":
                            volinfo[volname]["options"][opt] = el.text
            else:
                volinfo[volname][elem.tag] = elem.text
    return volinfo
