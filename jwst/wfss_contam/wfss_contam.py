#
#  Top level module for WFSS contamination correction.
#
import logging
import multiprocessing
import numpy as np

from jwst import datamodels
from .observations import Observation
from .sens1d import get_photom_data

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


def contam_corr(input_model, waverange, photom, max_cores):
    """
    The main WFSS contamination correction function

    Parameters
    ----------
    input_model : `~jwst.datamodels.MultiSlitModel`
        Input data model containing 2D spectral cutouts
    waverange : `~jwst.datamodels.WavelengthrangeModel`
        Wavelength range reference file model
    photom : `~jwst.datamodels.NrcWfssPhotomModel` or `~jwst.datamodels.NisWfssPhotomModel`
        Photom (flux cal) reference file model
    max_cores : string
        Number of cores to use for multiprocessing. If set to 'none'
        (the default), then no multiprocessing will be done. The other
        allowable values are 'quarter', 'half', and 'all', which indicate
        the fraction of cores to use for multi-proc. The total number of
        cores includes the SMT cores (Hyper Threading for Intel).

    Returns
    -------
    output_model : `~jwst.datamodels.MultiSlitModel`
        A copy of the input_model that has been corrected
    simul_model : `~jwst.datamodels.ImageModel`
        Full-frame simulated image of the grism exposure
    contam_model : `~jwst.datamodels.MultiSlitModel`
        Contamination estimate images for each source slit

    """
    # Determine number of cpu's to use for multi-processing
    if max_cores == 'none':
        ncpus = 1
    else:
        num_cores = multiprocessing.cpu_count()
        if max_cores == 'quarter':
            ncpus = num_cores // 4 or 1
        elif max_cores == 'half':
            ncpus = num_cores // 2 or 1
        elif max_cores == 'all':
            ncpus = num_cores
        else:
            ncpus = 1
        log.debug(f"Found {num_cores} cores; using {ncpus}")

    # Initialize output model
    output_model = input_model.copy()

    # Get the segmentation map for this grism exposure
    seg_model = datamodels.open(input_model.meta.segmentation_map)

    # Get the direct image from which the segmentation map was constructed
    direct_file = input_model.meta.direct_image
    image_names = [direct_file]
    log.debug(f"Direct image names={image_names}")

    # Get the grism WCS from the input model
    grism_wcs = input_model.slits[0].meta.wcs

    # Find out how many spectral orders are defined, based on the
    # array of order values in the Wavelengthrange ref file
    spec_orders = np.asarray(waverange.order)
    spec_orders = spec_orders[spec_orders != 0]  # ignore any order 0 entries
    log.debug(f"Spectral orders defined = {spec_orders}")

    # Get the FILTER and PUPIL wheel positions, for use later
    filter_kwd = input_model.meta.instrument.filter
    pupil_kwd = input_model.meta.instrument.pupil

    # NOTE: The NIRCam WFSS mode uses filters that are in the FILTER wheel
    # with gratings in the PUPIL wheel. NIRISS WFSS mode, however, is just
    # the opposite. It has gratings in the FILTER wheel and filters in the
    # PUPIL wheel. So when processing NIRISS grism exposures the name of
    # filter needs to come from the PUPIL keyword value.
    if input_model.meta.instrument.name == 'NIRISS':
        filter_name = pupil_kwd
    else:
        filter_name = filter_kwd

    # Load lists of wavelength ranges and flux cal info for all orders
    wmin = {}
    wmax = {}
    sens_waves = {}
    sens_response = {}
    for order in spec_orders:
        wavelength_range = waverange.get_wfss_wavelength_range(filter_name, [order])
        wmin[order] = wavelength_range[order][0]
        wmax[order] = wavelength_range[order][1]
        # Load the sensitivity (inverse flux cal) data for this mode and order
        sens_waves[order], sens_response[order] = get_photom_data(photom, filter_kwd, pupil_kwd, order)
    log.debug(f"wmin={wmin}, wmax={wmax}")

    # Initialize the simulated image object
    simul_all = None
    obs = Observation(image_names, seg_model, grism_wcs, filter_name,
                      boundaries=[0, 2047, 0, 2047], max_cpu=ncpus)

    # Create dict of offsets to pass to disperse_all to create simulated image
    # with sources in correct locations
    offset_dict = dict()
    for slit in output_model.slits:
        offset_dict[int(slit.source_id)] = (slit.xstart - 1, slit.ystart - 1)

    # Create simulated grism image for each order and sum them up
    for order in spec_orders:

        log.info(f"Creating full simulated grism image for order {order}")
        obs.disperse_all(order, wmin[order], wmax[order], sens_waves[order],
                         sens_response[order], offset_dict)

        # Accumulate result for this order into the combined image
        if simul_all is None:
            simul_all = obs.simulated_image
        else:
            simul_all += obs.simulated_image

    # Save the full-frame simulated grism image
    simul_model = datamodels.ImageModel(data=simul_all)
    simul_model.update(input_model, only="PRIMARY")

    # Loop over all slits/sources to subtract contaminating spectra
    log.info("Creating contamination image for each individual source")
    contam_model = datamodels.MultiSlitModel()
    contam_model.update(input_model)
    slits = []
    for slit in output_model.slits:

        # Create simulated spectrum for this source only
        sid = slit.source_id
        order = slit.meta.wcsinfo.spectral_order
        chunk = np.where(obs.IDs == sid)[0][0]  # find chunk for this source

        obs.simulated_image = np.zeros(obs.dims)
        obs.disperse_chunk(chunk, order, wmin[order], wmax[order],
                           sens_waves[order], sens_response[order], offset_dict[sid])
        this_source = obs.simulated_image

        # Contamination estimate is full simulated image minus this source
        contam = simul_all - this_source

        # Create a cutout of the contam image that matches the extent
        # of the source slit
        x2 = offset_x + slit.xsize
        y2 = offset_y + slit.ysize
        cutout = contam[offset_y:y2, offset_x:x2]
        new_slit = datamodels.SlitModel(data=cutout)
        copy_slit_info(slit, new_slit)
        slits.append(new_slit)

        # Subtract the cutout from the source slit
        slit.data -= cutout

    # Save the contamination estimates for all slits
    contam_model.slits.extend(slits)

    # Set the step status to COMPLETE
    output_model.meta.cal_step.wfss_contam = 'COMPLETE'

    return output_model, simul_model, contam_model


def copy_slit_info(input_slit, output_slit):

    """Copy meta info from one slit to another.

    Parameters
    ----------
    input_slit : SlitModel
        Input slit model from which slit-specific info will be copied

    output_slit : SlitModel
        Output slit model to which slit-specific info will be copied

    """
    output_slit.name = input_slit.name
    output_slit.xstart = input_slit.xstart
    output_slit.ystart = input_slit.ystart
    output_slit.xsize = input_slit.xsize
    output_slit.ysize = input_slit.ysize
    output_slit.source_id = input_slit.source_id
    output_slit.source_type = input_slit.source_type
    output_slit.source_xpos = input_slit.source_xpos
    output_slit.source_ypos = input_slit.source_ypos
    output_slit.meta.wcsinfo.spectral_order = input_slit.meta.wcsinfo.spectral_order
    output_slit.meta.wcsinfo.dispersion_direction = input_slit.meta.wcsinfo.dispersion_direction
    output_slit.meta.wcs = input_slit.meta.wcs
