import numpy as np
import cv2
from pycalib.util import *

def undistort_points(pt2d, cameraMatrix, distCoeffs):
    return cv2.undistortPoints(pt2d, cameraMatrix, distCoeffs, P=cameraMatrix)


def distort_points(pt2d, cameraMatrix, distCoeffs):
    assert False, "not tested"
    # a bit tricky.
    # http://answers.opencv.org/question/148670/re-distorting-a-set-of-points-after-camera-calibration/

    # step1. **undistort** without dist & P to get normalized coord.
    n2d = cv2.undistortPoints(pt2d, cameraMatrix, distCoeffs=None, P=None)

    # step2. get homogeneous coord
    n3d = cv2.convertPointsToHomogeneous(n2d)

    # step3. project WITH dist, and R=I, t=0
    pt2d_d = cv2.projectPoints(n3d, np.zeros(3), np.zeros(3), cameraMatrix, distCoeffs)

    return pt2d_d

def excalib(p1, p2, A, d):
    """
    Returns R, t satisfying x2 = R * x1 + t (= p1 will be the world camera)
    """
    p1 = transpose_to_col(p1, 2).reshape((-1,1,2)).astype(np.float)
    p2 = transpose_to_col(p2, 2).reshape((-1,1,2)).astype(np.float)

    # Undistort
    n1 = undistort_points(p1, A, d)
    n2 = undistort_points(p2, A, d)

    E, status = cv2.findEssentialMat(n1, n2, A, method=cv2.RANSAC, prob=0.999, threshold=3.0)
    _, R, t, _ = cv2.recoverPose(E, n1, n2, A, mask=status)
    return R, t, E, status

def recoverPose2(E, n1, n2, K1, K2, mask):
    n1 = n1.reshape((-1, 2))
    n2 = n2.reshape((-1, 2))
    R2a, R2b, t2 = cv2.decomposeEssentialMat(E)
    R1 = np.eye(3)
    t1 = np.zeros((3,1))

    def z_count(R1, t1, R2, t2, K1, K2, n1, n2):
        """
        Count number of points appeared in front of the cameras
        """
        P1 = K1 @ np.hstack((R1, t1))
        P2 = K2 @ np.hstack((R2, t2))
        Xh1 = cv2.triangulatePoints(P1, P2, n1, n2)
        Xh1 /= Xh1[3,:]
        z1 = np.sum(Xh1[2,:]>0)  # num of positive z points in Cam1 coordinate system
        Xh2 = R2 @ Xh1[:3,:] + t2
        z2 = np.sum(Xh2[2,:]>0)  # num of positive z points in Cam2 coordinate system
        return (z1 + z2), Xh1[:3,:]

    zmax = -1
    for R2x, t2x in [[R2a, t2], [R2a, -t2], [R2b, t2], [R2b, -t2]]:
        z, Xx = z_count(R1, t1, R2x,  t2x, K1, K2, n1.T, n2.T)
        if zmax < z:
            zmax = z
            R2_est = R2x
            t2_est = t2x
            X_est = Xx
            
    return R2_est, t2_est

def excalib2(p1, p2, A1, d1, A2, d2):
    """
    Returns R, t satisfying x2 = R * x1 + t (= p1 will be the world camera)
    """
    p1 = transpose_to_col(p1, 2).reshape((-1,1,2)).astype(np.float)
    p2 = transpose_to_col(p2, 2).reshape((-1,1,2)).astype(np.float)

    # Undistort
    n1 = undistort_points(p1, A1, d1)
    n2 = undistort_points(p2, A2, d2)
    
    # Find E
    F, status = cv2.findFundamentalMat(n1, n2, cv2.FM_RANSAC)
    E = A2.T @ F @ A1
    E = E / np.linalg.norm(E)

    # Decompose E
    R, t = recoverPose2(E, n1, n2, A1, A2, mask=status)
    return R, t, E, status

def skew(x):
    """
    Returns the skew-symmetric matrix [x]_\times of vector x.
    """
    return np.array([[0, -x[2], x[1]], [x[2], 0, -x[0]], [-x[1], x[0], 0]])


def pose_registration_R(N, Rt_pairs):
    """
    Subfunction for pose_registration
    """
    A = []
    for (i, j), Rt in Rt_pairs.items():
        Rij = Rt[0:3,0:3]
        x = np.zeros((3, N*3))
        x[:, i*3:i*3+3] = -Rij
        x[:, j*3:j*3+3] = np.eye(3)
        A.append(x)
    A = np.vstack(A)

    # solve Ax=0
    w, v = np.linalg.eigh(A.T @ A)
    err = np.sum(w[0:3]) / np.sum(w)
    R = v[:,0:3]

    # find a set of coeffs to make R0 be I
    k = np.linalg.inv(R[:3, :3])
    R = R @ k

    # force R to be SO(3)
    for i in range(N):
        u, _, vt = np.linalg.svd(R[i*3:i*3+3,:])
        R[i*3:i*3+3,:] = u @ vt

    # let R[0] be identity
    k = np.linalg.inv(R[0:3,0:3])
    for i in range(1, N):
        R[i*3:i*3+3,:] = R[i*3:i*3+3,:] @ k
    R[0:3,0:3] = np.eye(3,3)

    return R, err


def pose_registration_T(N, Rt_pairs, R_w2c):
    """
    Subfunction for pose_registration

    Does not work if camera motion is collinear
    """

    B = []
    for (i, j), Rt in Rt_pairs.items():
        Tij = Rt[0:3,3]
        Ri = R_w2c[3*i:3*i+3]
        Rj = R_w2c[3*j:3*j+3]
        # T vector -> skew-symmetric matrix
        Tij = skew(Tij)
        x = np.zeros((3, N*3))
        x[:, 3*i:3*i+3] = Tij @ Rj @ Ri.T
        x[:, 3*j:3*j+3] = -Tij
        B.append(x)
    B = np.vstack(B)

    # solve Bx=0
    _, s, vt = np.linalg.svd(B.T @ B)
    err = np.sum(s[-4:]) / np.sum(s)
    # null-space has 4-dim = any-translation for x/y/z + global-scale
    k = vt.T[:,-4:]
    # find a set of coeffs to make t0 be (0, 0, 0)
    _, s, vt = np.linalg.svd(k[0:3,:])
    T = k @ vt[3,:].T
    T = T / np.linalg.norm(T[3:6])

    # fix T sign using the 1st pair
    for (i, j), Rt in Rt_pairs.items():
        Tij = Rt[0:3,3]

        Ri = R_w2c[3*i:3*i+3,:]
        Rj = R_w2c[3*j:3*j+3,:]
        Ti = T[3*i:3*i+3]
        Tj = T[3*j:3*j+3]

        # compare Tij with the estimated one
        t = - Ti + Ri @ Rj.T @ Tj
        if t @ Tij < 0:
            T = -T
        T[0:3] = 0

        # return immediately in the loop
        return T, err


def pose_registration(N, Rt_pairs, get_c2w=False):
    """
    Global pose registration from pair-wise R_ij, t_ij. The output R_i, t_i are W2C by default, i.e., they satisfy x_i = R_i x_w + t_i .

    Parameters
    ----------
    N : int
        Number of cameras
    Rt_pairs : dict
        2D dict of R_ij, t_ij where Rt_pairs[i,j] holds R_ij, t_ij satisfying x_j = R_ij x_i + t_ij .
    get_c2w : Bool
        Output C2W rotation and translation. That is, R_i and t_i satisfy x_w = R_i x_i + t_i .

    Returns
    -------
    R : ndarray
        3N x 3 array of rotation matrices
    t : ndarray
        3N x 1 array of translation vectors

    Notes
    -----
    Martinec and Padjla. "Robust Rotation and Translation Estimation in Multiview Reconstruction," CVPR 2007.
    Nianjuan Jiang, Zhaopeng Cui, and Ping Tan. "A global linear method for camera pose registration," ICCV 2013.
    """

    #print(Rt_pairs)
    R_w2c, Rerr = pose_registration_R(N, Rt_pairs)
    #print(R_w2c, Rerr)
    T_w2c, Terr = pose_registration_T(N, Rt_pairs, R_w2c)
    #print(T_w2c, Terr)

    # W2C -> C2W
    if get_c2w is True:
        assert False, "not tested"
        for i in range(N):
            R[i*3:i*3+3,:] = R[i*3:i*3+3,:].T
            T[i*3:i*3+3] = - R[i*3:i*3+3,:] @ T[i*3:i*3+3]

    return R_w2c, T_w2c.reshape((-1,1)), Rerr, Terr

def quat2mat(q):
    """
    Quaternion to rotation matrix conversion
    """
    x, y, z, w = q[0], q[1], q[2], q[3]
    return np.array([
        [1 - 2*y*y - 2*z*z,      2*x*y + 2*w*z,      2*x*z - 2*w*y],
        [    2*x*y - 2*w*z,  1 - 2*x*x - 2*z*z,      2*y*z + 2*w*x],
        [    2*x*z + 2*w*y,      2*y*z - 2*w*x,  1 - 2*x*x - 2*y*y]])


def relativize(R0_w2c, t0_w2c, R_w2c, t_w2c):
    """R_w2c, t_w2cをR0_w2c, t0_w2c基準に変換する．
    つまりR0_w2c, t0_w2c側を世界座標系とした場合の，R_w2c, t_w2cを得る．
    入力する各R_w2c, t_w2cは R_w2c*x + t_w2c で世界座標からローカル座標へ変換するとする．
    """
    assert R0_w2c.shape == (3, 3)
    assert R_w2c.shape == (3, 3)
    assert t0_w2c.size == 3
    assert t_w2c.size == 3

    t0_w2c = t0_w2c.reshape((3, 1))
    t_w2c = t_w2c.reshape((3, 1))

    if np.allclose(R0_w2c, R_w2c):
        R = np.eye(3)
        if np.allclose(t0_w2c, t_w2c):
            return R, np.zeros(t_w2c.shape)
    else:
        R = R_w2c @ R0_w2c.T

    return R, t_w2c - R @ t0_w2c


def triangulate(pt2d, P):
    """
    Triangulate a 3D point from two or more views by DLT.
    """
    N = len(pt2d)
    assert N == len(P)

    AtA = np.zeros((4, 4))
    x = np.zeros((2, 4))
    for i in range(N):
        x[0,:] = P[i][0,:] - pt2d[i][0] * P[i][2,:]
        x[1,:] = P[i][1,:] - pt2d[i][1] * P[i][2,:]
        AtA += x.T @ x
    
    _, v = np.linalg.eigh(AtA)
    if np.isclose(v[3, 0], 0):
        return v[:,0]
    else:
        return v[:,0] / v[3,0]


def reprojection_error(pt3d, pt2d, P):
    N = len(pt2d)
    err = []
    for i in range(N):
        x = P[i] @ pt3d
        x /= x[2]
        x[0] -= pt2d[i][0]
        x[1] -= pt2d[i][1]
        err.append(x[0:2])
    return err


class Camera:
    __W2C = np.zeros((3, 4))
    __A = np.eye(3)
    __d = np.zeros(5)

    def __init__(self):
        pass

    def set_A(self, *, f=None, u0=None, v0=None, A=None):
        if f is not None:
            assert u0 is not None
            assert v0 is not None
            __A = np.array([[f, 0, u0], [0, f, v0], [0, 0, 1]])
        if A is not None:
            __A = A

    def set_d(self, *, dist_coeffs=None):
        if dist_coeffs is not None:
            __d = dist_coeffs

    def undistort_points(self, pt2d):
        return cv2.undistortPoints(pt2d, cameraMatrix=self.__A, distCoeffs=self.__d, P=self.__A)

    def distort_points(self, pt2d):
        # a bit tricky.
        # http://answers.opencv.org/question/148670/re-distorting-a-set-of-points-after-camera-calibration/

        # step1. **undistort** without dist & P to get normalized coord.
        n2d = cv2.undistortPoints(pt2d, cameraMatrix=self.__A, distCoeffs=None, P=None)

        # step2. get homogeneous coord
        n3d = cv2.convertPointsToHomogeneous(n2d)

        # step3. project WITH dist, and R=I, t=0
        pt2d_d = cv2.projectPoints(n3d, np.zeros(3), np.zeros(3), cameraMatrix=self.__A, distCoeffs=self.__d)

        return pt2d_d



def lookat(eye, center, up):
    eye = transpose_to_col(eye, 3)
    center = transpose_to_col(center, 3)
    up = transpose_to_col(up, 3)
    
    ez = center - eye
    ez = ez / np.linalg.norm(ez)
    
    ey = up
    ey = ey / np.linalg.norm(ey)

    ex = np.cross(ey.T, ez.T).reshape((3,1))
    ex = ex / np.linalg.norm(ex)
    
    ey = np.cross(ez.T, ex.T).reshape((3,1))
    ey = ey / np.linalg.norm(ey)
    
    t_c2w = eye
    R_c2w = np.hstack((ex, ey, ez))

    return R_c2w.T, -R_c2w.T @ t_c2w
